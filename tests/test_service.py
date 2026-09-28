"""Service control never touches a real LaunchAgent, and development servers stay isolated."""
import http.server
import json
import os
import plistlib
import shutil
import signal
import socket
import subprocess
import sys
import threading
import urllib.request
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bardic import service
from bardic.app import create_app

REAL_WAIT = service.wait_for
REAL_ALIVE = service.alive
REAL_LISTENER = service.listener
SERVER_PID = 4242  # The fake launchd job's process; never signalled.

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX process control")


class FakeLaunchd:
    """launchctl as a state machine; no real launchd job is created."""

    def __init__(self, loaded=False):
        self.loaded = loaded
        self.calls = []

    def __call__(self, *args, timeout=30):
        self.calls.append(args)
        if args[0] == "print":
            if not self.loaded:
                return subprocess.CompletedProcess(args, 113, "", "not found")
            return subprocess.CompletedProcess(
                args, 0, f"\tstate = running\n\tpid = {SERVER_PID}\n\tlast exit code = (never exited)\n\t\tstate = active\n", "")
        if args[0] == "bootout":
            self.loaded = False
        if args[0] == "bootstrap":
            self.loaded = True
        return subprocess.CompletedProcess(args, 0, "", "")

    def verbs(self):
        return [call[:1] for call in self.calls if call[0] != "print"]


def make_checkout(path: Path, env: str = "") -> Path:
    (path / "bardic").mkdir(parents=True)
    (path / "bardic" / "__main__.py").write_text("")
    (path / ".git").mkdir()
    if env:
        (path / ".env").write_text(env)
    return path


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("BARDIC_DEV_HOME", str(tmp_path / "dev"))
    checkout = make_checkout(tmp_path / "checkout")
    fake = FakeLaunchd()
    synced = []
    monkeypatch.setattr(service, "main_checkout", lambda: checkout)
    monkeypatch.setattr(service, "linked_worktree", lambda: False)
    monkeypatch.setattr(service, "launchctl", fake)
    monkeypatch.setattr(service, "sync_environment", synced.append)
    monkeypatch.setattr(service, "listener", lambda port: SERVER_PID if fake.loaded else None)
    monkeypatch.setattr(service, "alive", lambda pid: pid != SERVER_PID and REAL_ALIVE(pid))
    monkeypatch.setattr(service, "wait_for", lambda condition, timeout, interval=0.5: condition())
    return {"home": home, "checkout": checkout, "launchd": fake, "synced": synced}


@pytest.fixture
def macos(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")


def detached(*argv) -> int:
    """Start a process the way a hand launch leaves one: not our child, so init reaps it."""
    result = subprocess.run(["/bin/sh", "-c", '"$@" >/dev/null 2>&1 & echo $!', "sh", *argv],
                            capture_output=True, text=True, check=True)
    return int(result.stdout.strip())


def kill(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


@pytest.fixture
def fake_bardic():
    pid = detached(sys.executable, "-c", "import time; time.sleep(60)", "-m", "bardic")
    yield pid
    kill(pid)


def install_plist(home: Path, checkout: Path) -> None:
    path = home / "Library" / "LaunchAgents" / f"{service.LABEL}.plist"
    path.parent.mkdir(parents=True)
    path.write_bytes(plistlib.dumps(service.service_plist(checkout, home / "bardic.log")))


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def listening_addresses(port: int) -> str:
    return service._output(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"])


def test_launch_agent_runs_the_server_itself_with_a_path_for_macos_tools(tmp_path):
    plist = service.service_plist(tmp_path, tmp_path / "bardic.log")
    assert plistlib.loads(plistlib.dumps(plist)) == plist
    # launchd's SIGKILL after ExitTimeOut must reach Bardic, not a `uv run` wrapper with an orphaned child.
    assert plist["ProgramArguments"] == [str(tmp_path / ".venv" / "bin" / "python"), "-m", "bardic"]
    assert plist["WorkingDirectory"] == str(tmp_path)
    path = plist["EnvironmentVariables"]["PATH"].split(":")
    assert {"/usr/bin", "/usr/sbin", "/opt/homebrew/bin"} <= set(path) and len(path) == len(set(path))
    # A requested stop stays stopped; a crash restarts. Shutdown outlasts analysis and ordinary narration requests.
    assert plist["KeepAlive"] == {"SuccessfulExit": False} and plist["RunAtLoad"] is True
    assert plist["ExitTimeOut"] >= 240
    assert plist["StandardOutPath"] == plist["StandardErrorPath"] == str(tmp_path / "bardic.log")
    assert "GEMINI_API_KEY" not in plist["EnvironmentVariables"]  # Keys stay in the checkout's .env.


def test_settings_are_read_from_the_checkout_env_like_the_launcher(tmp_path):
    checkout = make_checkout(tmp_path / "a", "SPINTAILS_PORT=9000\nBARDIC_PORT=8766\nBARDIC_DATA_DIR=library\n"
                                             "BARDIC_LAN_NAME=Bardic.local\nBARDIC_HOST=192.168.0.160\n")
    settings = service.checkout_settings(checkout)
    assert settings["port"] == 8766
    assert settings["library"] == checkout / "library"
    assert settings["lan_name"] == "bardic"
    assert settings["probe_host"] == "192.168.0.160"

    absolute = make_checkout(tmp_path / "b", f"SPINTAILS_DATA_DIR={tmp_path / 'elsewhere'}\n")
    assert service.checkout_settings(absolute)["library"] == tmp_path / "elsewhere"
    assert service.checkout_settings(absolute)["port"] == 8765
    assert service.checkout_settings(absolute)["probe_host"] == "127.0.0.1"

    legacy = make_checkout(tmp_path / "c")
    assert service.checkout_settings(legacy)["library"] == legacy / ".bardic"
    (legacy / ".spintails").mkdir()
    assert service.checkout_settings(legacy)["library"] == legacy / ".spintails"


def test_development_environment_blanks_keys_and_network_settings(tmp_path):
    base = {"PATH": "/usr/bin", "GEMINI_API_KEY": "test-key", "OPENAI_API_KEY": "test-key", "BREEZE_TTS_URL": "http://gpu:7860",
            "BARDIC_LAN_NAME": "bardic", "BARDIC_HOST": "0.0.0.0", "SPINTAILS_DATA_DIR": "/live", "SPINTAILS_PORT": "8765"}
    env = service.dev_environment(base, 8771, tmp_path)
    assert env["PATH"] == "/usr/bin"
    assert all(env[name] == "" for name in service.DEV_BLANK)
    assert env["BARDIC_PORT"] == "8771" and env["BARDIC_DATA_DIR"] == str(tmp_path)
    assert "SPINTAILS_DATA_DIR" not in env and "SPINTAILS_PORT" not in env
    assert base["GEMINI_API_KEY"] == "test-key"


def test_development_keys_come_only_from_the_service_checkouts_provider_settings(tmp_path, isolated, monkeypatch):
    live = tmp_path / "live"
    (isolated["checkout"] / ".env").write_text(
        f"GEMINI_API_KEY=test-key-not-real\nOPENAI_API_KEY=\nBREEZE_TTS_URL=http://gpu:7860\nBARDIC_BOOKNLP_URL=http://gpu:8100\n"
        f"BARDIC_DATA_DIR={live}\nBARDIC_PORT=8765\nBARDIC_LAN_NAME=bardic\nBARDIC_HOST=0.0.0.0\n")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "the-calling-agents-key")
    providers = service.provider_settings(isolated["checkout"])
    assert providers == {"GEMINI_API_KEY": "test-key-not-real", "BREEZE_TTS_URL": "http://gpu:7860",
                         "BARDIC_BOOKNLP_URL": "http://gpu:8100"}

    env = service.dev_environment({"PATH": "/usr/bin", "ANTHROPIC_API_KEY": "the-calling-agents-key"},
                                  8771, tmp_path / "scratch", providers)
    assert env["GEMINI_API_KEY"] == "test-key-not-real" and env["BREEZE_TTS_URL"] == "http://gpu:7860"
    assert env["ANTHROPIC_API_KEY"] == "" and env["OPENAI_API_KEY"] == ""
    assert env["BARDIC_LAN_NAME"] == env["BARDIC_HOST"] == env["BARDIC_ALLOWED_HOSTS"] == ""
    assert env["BARDIC_PORT"] == "8771" and env["BARDIC_DATA_DIR"] == str(tmp_path / "scratch")

    (isolated["checkout"] / ".env").write_text("OPENAI_API_KEY=\nBARDIC_PORT=8765\n")
    with pytest.raises(service.CommandError, match="no provider keys"):
        service.provider_settings(isolated["checkout"])


def test_library_overlap_ignores_spelling_links_and_nesting(tmp_path):
    live = tmp_path / "live"
    (live / "audio").mkdir(parents=True)
    (tmp_path / "link").symlink_to(live)
    for alias in (live, tmp_path / "link", live / "audio", live / "not-yet" / "deeper", tmp_path,
                  tmp_path / "live" / ".." / "live"):
        assert service.overlaps(alias, live), alias
    if (tmp_path / "LIVE").exists():  # A case-insensitive volume, as macOS uses by default.
        assert service.overlaps(tmp_path / "LIVE", live)
    (tmp_path / "other").mkdir()
    assert not service.overlaps(tmp_path / "other", live)
    assert not service.overlaps(tmp_path / "scratch" / "library", tmp_path / "missing")


def test_install_refuses_worktrees_and_other_directories_before_calling_launchd(tmp_path, isolated, macos, capsys):
    worktree = tmp_path / "worktree"
    (worktree / "bardic").mkdir(parents=True)
    (worktree / "bardic" / "__main__.py").write_text("")
    (worktree / ".git").write_text("gitdir: /elsewhere\n")
    assert service.main(["install", "--checkout", str(worktree)]) == 1
    assert "linked worktree" in capsys.readouterr().err
    assert service.main(["install", "--checkout", str(tmp_path)]) == 1
    assert "not a Bardic checkout" in capsys.readouterr().err
    assert isolated["launchd"].calls == [] and isolated["synced"] == []
    assert not (isolated["home"] / "Library" / "LaunchAgents").exists()


def test_install_writes_the_agent_for_the_main_checkout_and_waits_for_it(isolated, macos, monkeypatch, capsys):
    monkeypatch.setattr(service, "probe", lambda host, port, timeout=5.0: [])
    assert service.main(["install"]) == 0
    plist = plistlib.loads((isolated["home"] / "Library" / "LaunchAgents" / "local.bardic.plist").read_bytes())
    assert plist["WorkingDirectory"] == str(isolated["checkout"])
    assert isolated["synced"] == [isolated["checkout"]]
    assert isolated["launchd"].verbs() == [("bootstrap",)]
    assert "http://127.0.0.1:8765" in capsys.readouterr().out


def test_commands_that_interrupt_the_service_need_consent_from_a_worktree(isolated, macos, monkeypatch, capsys):
    install_plist(isolated["home"], isolated["checkout"])
    isolated["launchd"].loaded = True
    monkeypatch.setattr(service, "linked_worktree", lambda: True)
    monkeypatch.setattr(service, "probe", lambda host, port, timeout=5.0: [])
    for command in (["stop"], ["restart"], ["install"], ["uninstall"]):
        assert service.main(command) == 1
        assert "--yes" in capsys.readouterr().err
    assert isolated["launchd"].verbs() == []
    assert service.main(["status"]) == 0
    assert service.main(["restart", "--yes"]) == 0
    assert isolated["launchd"].verbs() == [("bootout",), ("bootstrap",)]


def test_stop_and_restart_refuse_active_jobs_unless_forced(isolated, macos, monkeypatch, capsys):
    install_plist(isolated["home"], isolated["checkout"])
    isolated["launchd"].loaded = True
    monkeypatch.setattr(service, "probe", lambda host, port, timeout=5.0: [{"kind": "listen_chapter", "status": "running"}])
    assert service.main(["restart"]) == 1
    assert service.main(["stop"]) == 1
    assert "listen_chapter running" in capsys.readouterr().err
    assert isolated["launchd"].verbs() == [] and isolated["synced"] == []

    assert service.main(["restart", "--force"]) == 0
    assert service.main(["stop", "--force"]) == 0
    assert isolated["launchd"].verbs() == [("bootout",), ("bootstrap",), ("bootout",)]


def test_unreadable_jobs_are_not_treated_as_none(isolated, macos, monkeypatch, capsys):
    install_plist(isolated["home"], isolated["checkout"])
    isolated["launchd"].loaded = True
    monkeypatch.setattr(service, "probe", lambda host, port, timeout=5.0: None)
    assert service.main(["stop"]) == 1
    assert "Could not read active jobs" in capsys.readouterr().err
    assert isolated["launchd"].verbs() == []
    assert service.main(["stop", "--force"]) == 0


def test_stop_waits_until_the_server_process_has_exited(isolated, macos, monkeypatch, capsys):
    install_plist(isolated["home"], isolated["checkout"])
    isolated["launchd"].loaded = True
    monkeypatch.setattr(service, "probe", lambda host, port, timeout=5.0: [])
    monkeypatch.setattr(service, "wait_for", lambda condition, timeout, interval=0.5: REAL_WAIT(condition, 2, 0.01))
    checks = []
    monkeypatch.setattr(service, "alive", lambda pid: checks.append(pid) or len(checks) < 4)
    assert service.main(["stop"]) == 0  # launchd dropped the job at once; the server needed three more checks.
    assert checks == [SERVER_PID] * 4

    isolated["launchd"].loaded = True
    monkeypatch.setattr(service, "alive", lambda pid: True)
    assert service.main(["stop"]) == 1
    assert f"pid {SERVER_PID}) has not finished stopping" in capsys.readouterr().err


def test_start_will_not_race_a_server_outside_launchd(isolated, macos, monkeypatch, fake_bardic, capsys):
    install_plist(isolated["home"], isolated["checkout"])
    monkeypatch.setattr(service, "listener", lambda port: fake_bardic)
    assert service.main(["start"]) == 1
    assert "outside launchd" in capsys.readouterr().err
    assert service.main(["restart"]) == 1
    assert isolated["launchd"].verbs() == []
    assert service.alive(fake_bardic)


def test_restart_does_not_adopt_an_unmanaged_server(isolated, macos, monkeypatch, fake_bardic, capsys):
    monkeypatch.setattr(service, "listener", lambda port: fake_bardic)
    assert service.main(["restart"]) == 1
    assert "stop, then ./bardicctl install" in capsys.readouterr().err
    assert service.alive(fake_bardic)


def test_stop_signals_an_unmanaged_bardic_server_but_nothing_else(isolated, monkeypatch, fake_bardic, capsys):
    monkeypatch.setattr(service, "wait_for", lambda condition, timeout, interval=0.5: REAL_WAIT(condition, 10, 0.1))
    monkeypatch.setattr(service, "probe", lambda host, port, timeout=5.0: [])
    other = detached("sleep", "60")
    try:
        monkeypatch.setattr(service, "listener", lambda port: other)
        assert service.main(["stop"]) == 1
        assert "not Bardic" in capsys.readouterr().err
        assert service.alive(other)
    finally:
        kill(other)

    monkeypatch.setattr(service, "listener", lambda port: fake_bardic)
    assert service.main(["stop"]) == 0
    assert "not managed by launchd" in capsys.readouterr().out
    assert not service.alive(fake_bardic)


def test_probe_asks_for_every_active_job_as_a_loopback_host():
    seen = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append((self.path, self.headers["Host"]))
            body = b'[{"status": "running"}]'
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        assert service.probe("127.0.0.1", server.server_address[1]) == [{"status": "running"}]
    finally:
        server.shutdown()
    # A server bound to one network address rejects that address as a Host; loopback is always trusted.
    assert seen == [("/api/jobs?active=true", "127.0.0.1")]


def test_active_jobs_are_listed_beyond_the_hundred_most_recent(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        store = client.app.state.runtime.store
        running = store.create_job("book", "analysis")
        store.update_job(running["id"], status="running")
        for _ in range(120):
            store.update_job(store.create_job("book", "listen")["id"], status="completed")
        assert running["id"] not in {job["id"] for job in client.get("/api/jobs").json()}
        assert [job["id"] for job in client.get("/api/jobs", params={"active": "true"}).json()] == [running["id"]]


def test_a_reused_process_id_is_not_mistaken_for_a_development_server(fake_bardic):
    record = {"pid": fake_bardic, "started": service.started_at(fake_bardic)}
    assert service.dev_running(record)
    assert not service.dev_running({**record, "started": "Mon Jan  1 00:00:00 2001"})
    other = detached("sleep", "60")
    try:
        assert not service.dev_running({"pid": other, "started": service.started_at(other)})
    finally:
        kill(other)


def test_development_server_refuses_the_service_library_and_port(tmp_path, isolated, capsys):
    live = tmp_path / "live"
    live.mkdir()
    (isolated["checkout"] / ".env").write_text(f"BARDIC_DATA_DIR={live}\nBARDIC_PORT=8766\n")
    for library in (live, tmp_path, live / "books"):
        assert service.main(["dev", "start", "--name", "t", "--library", str(library)]) == 1
        assert "overlaps the service's library" in capsys.readouterr().err
    assert service.main(["dev", "start", "--name", "t", "--port", "8766"]) == 1
    assert "belongs to the service" in capsys.readouterr().err
    assert not (live / "books").exists() and not (tmp_path / "dev" / "t").exists()


def test_default_name_does_not_act_on_another_checkouts_server(tmp_path, capsys):
    record = tmp_path / "dev" / service.PROJECT_ROOT.name / "instance.json"
    record.parent.mkdir(parents=True)
    record.write_text(json.dumps({"name": service.PROJECT_ROOT.name, "pid": 1, "started": "never", "port": 8770,
                                  "checkout": "/another/checkout", "library": "/x", "log": "/x.log"}))
    for command in (["dev", "start"], ["dev", "stop"], ["dev", "restart"]):
        assert service.main(command) == 1
        assert "belongs to /another/checkout" in capsys.readouterr().err


def test_development_start_fails_when_another_server_answers_on_its_port(tmp_path, monkeypatch, capsys):
    """A server that takes the chosen port first must not pass for ours, and our failed child must not linger."""
    monkeypatch.setattr(service, "wait_for", REAL_WAIT)
    blocker = http.server.ThreadingHTTPServer(("127.0.0.1", free_port()), http.server.BaseHTTPRequestHandler)
    port = blocker.server_address[1]
    monkeypatch.setattr(service, "probe", lambda host, port, timeout=5.0: [])  # The impostor "answers".
    monkeypatch.setattr(service, "dev_port", lambda *args: port)
    try:
        assert service.main(["dev", "start", "--name", "race"]) == 1
    finally:
        blocker.server_close()
    assert "did not start" in capsys.readouterr().err
    assert not service.dev_running(service.dev_record("race"))


def test_development_start_that_never_answers_is_stopped(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(service, "wait_for", REAL_WAIT)
    monkeypatch.setattr(service, "READY_TIMEOUT", 2)
    monkeypatch.setattr(service, "probe", lambda host, port, timeout=5.0: None)
    assert service.main(["dev", "start", "--name", "slow"]) == 1
    assert "did not start" in capsys.readouterr().err
    record = service.dev_record("slow")
    assert not REAL_ALIVE(record["pid"]) and not service.port_in_use(record["port"])


def test_development_server_runs_isolated_and_restarts_with_its_library(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(service, "wait_for", REAL_WAIT)
    monkeypatch.setattr(service, "alive", REAL_ALIVE)
    monkeypatch.setattr(service, "listener", REAL_LISTENER)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")
    monkeypatch.setenv("BARDIC_LAN_NAME", "bardic-test-unused")
    copy = tmp_path / "restored-copy"
    try:
        assert service.main(["dev", "start", "--name", "t1", "--library", str(copy)]) == 0
        first = service.dev_record("t1")
        assert "http://127.0.0.1:" in capsys.readouterr().out
        assert first["library"] == str(copy) and (copy / "library.sqlite3").exists()
        assert service.DEV_PORTS.start <= first["port"] < service.DEV_PORTS.stop
        assert service.dev_running(first)
        addresses = listening_addresses(first["port"])
        assert f"127.0.0.1:{first['port']}" in addresses and "*:" not in addresses  # Loopback only.
        with urllib.request.urlopen(f"http://127.0.0.1:{first['port']}/api/status", timeout=10) as response:
            assert json.load(response)["has_api_key"] is False
        assert service.main(["dev", "start", "--name", "t1"]) == 0  # Already running: no second server.
        assert "already running" in capsys.readouterr().out

        assert service.main(["dev", "restart", "--name", "t1"]) == 0
        second = service.dev_record("t1")
        assert second["pid"] != first["pid"] and not service.dev_running(first)
        assert (second["port"], second["library"]) == (first["port"], str(copy))
        assert service.main(["dev", "list"]) == 0
        listing = capsys.readouterr().out
        assert "t1" in listing and "running" in listing
    finally:
        assert service.main(["dev", "stop", "--name", "t1"]) == 0
    record = service.dev_record("t1")
    assert not service.dev_running(record) and not service.port_in_use(record["port"])
    assert "stopped" in capsys.readouterr().out


def test_development_server_with_keys_is_live_only_until_restarted_without_them(tmp_path, isolated, monkeypatch, capsys):
    monkeypatch.setattr(service, "wait_for", REAL_WAIT)
    monkeypatch.setattr(service, "alive", REAL_ALIVE)
    monkeypatch.setattr(service, "listener", REAL_LISTENER)
    (isolated["checkout"] / ".env").write_text("GEMINI_API_KEY=test-key-not-real\nBARDIC_LAN_NAME=bardic-test-unused\n")

    def has_key(record):
        with urllib.request.urlopen(f"http://127.0.0.1:{record['port']}/api/status", timeout=10) as response:
            return json.load(response)["has_api_key"]

    try:
        assert service.main(["dev", "start", "--name", "k", "--keys"]) == 0
        output = capsys.readouterr().out
        assert "GEMINI_API_KEY from" in output and "test-key-not-real" not in output
        first = service.dev_record("k")
        assert first["keys"] == ["GEMINI_API_KEY"] and has_key(first)
        assert "*:" not in listening_addresses(first["port"])  # Keys do not bring the network settings.
        assert service.main(["dev", "start", "--name", "k"]) == 0
        assert "Its keys are live" in capsys.readouterr().out
        assert service.main(["dev", "list"]) == 0
        assert "live" in capsys.readouterr().out

        assert service.main(["dev", "restart", "--name", "k"]) == 0
        second = service.dev_record("k")
        assert second["keys"] == [] and not has_key(second)
    finally:
        assert service.main(["dev", "stop", "--name", "k"]) == 0


def test_concurrent_development_starts_get_distinct_ports_and_records(tmp_path, monkeypatch):
    """Two sessions starting at once must not share a port or orphan a server by overwriting its record."""
    env = {**os.environ, "BARDIC_DEV_HOME": str(tmp_path / "dev"), "HOME": str(tmp_path / "home"),
           "PYTHONPATH": str(Path(service.__file__).resolve().parent.parent)}
    external = tmp_path / "copy"
    commands = [[sys.executable, "-P", "-m", "bardic.service", "dev", "start", "--name", name, *extra]
                for name, extra in (("a", []), ("b", ["--library", str(external)]))]
    starts = [subprocess.Popen(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
              for command in commands]
    outputs = [start.communicate(timeout=120)[0] for start in starts]
    monkeypatch.setattr(service, "wait_for", REAL_WAIT)
    monkeypatch.setattr(service, "alive", REAL_ALIVE)
    records = [service.dev_record("a"), service.dev_record("b")]
    try:
        assert [start.returncode for start in starts] == [0, 0], outputs
        assert records[0]["port"] != records[1]["port"]
        assert all(service.dev_running(record) for record in records)
        assert records[0]["library"] == str(tmp_path / "dev" / "a" / "library")
        assert records[1]["library"] == str(external) and (tmp_path / "dev" / "b" / "server.log").exists()
    finally:
        assert service.main(["dev", "stop", "--all"]) == 0
    assert not any(service.dev_running(record) for record in records if record)


@pytest.mark.skipif(shutil.which("uv") is None, reason="needs uv")
def test_wrapper_runs_this_checkout_from_any_directory(tmp_path):
    wrapper = Path(__file__).resolve().parent.parent / "bardicctl"
    result = subprocess.run([str(wrapper), "dev", "list"], cwd=tmp_path, capture_output=True, text=True, timeout=120,
                            env={**os.environ, "BARDIC_DEV_HOME": str(tmp_path / "dev")})
    assert result.returncode == 0, result.stderr
    assert "No development servers recorded." in result.stdout
