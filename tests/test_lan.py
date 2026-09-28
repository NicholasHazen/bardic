"""Network access is opt-in, trusts only named hosts, and withdraws its name on exit."""
import os
import socket
import subprocess
import sys
import time

import pytest
from fastapi.testclient import TestClient

from bardic import lan
from bardic.app import create_app


@pytest.fixture(autouse=True)
def loopback_environment(monkeypatch):
    for name in ("BARDIC_HOST", "BARDIC_LAN_NAME", "BARDIC_ALLOWED_HOSTS", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(name, raising=False)


def test_loopback_remains_the_default():
    assert lan.lan_name() is None
    assert lan.bind_host() == "127.0.0.1"
    assert lan.allowed_hosts() == []


@pytest.mark.parametrize("value", ["bardic", "Bardic.local", " bardic ", "BARDIC.LOCAL"])
def test_name_is_one_label_with_or_without_the_local_suffix(monkeypatch, value):
    monkeypatch.setenv("BARDIC_LAN_NAME", value)
    assert lan.lan_name() == "bardic"
    assert lan.bind_host() == "0.0.0.0"
    assert lan.allowed_hosts() == ["bardic.local"]


@pytest.mark.parametrize("value", ["my_bardic", "bardic.home", "-bardic", "bardic-", "bard ic", "a" * 64])
def test_names_that_mdns_cannot_publish_are_rejected(monkeypatch, value):
    monkeypatch.setenv("BARDIC_LAN_NAME", value)
    with pytest.raises(ValueError, match="BARDIC_LAN_NAME"):
        lan.lan_name()


def test_explicit_bind_and_extra_hosts(monkeypatch):
    monkeypatch.setenv("BARDIC_LAN_NAME", "bardic")
    monkeypatch.setenv("BARDIC_HOST", "192.168.0.160")
    monkeypatch.setenv("BARDIC_ALLOWED_HOSTS", " 192.168.0.160, MacBook-Pro-2.local ,,")
    assert lan.bind_host() == "192.168.0.160"
    assert lan.allowed_hosts() == ["bardic.local", "192.168.0.160", "macbook-pro-2.local"]


@pytest.mark.parametrize("value, message", [
    ("*", "wildcard"), ("*.local", "wildcard"),
    ("bardic.local:8766", "without a scheme or port"), ("http://bardic.local", "without a scheme or port"),
])
def test_extra_hosts_must_be_exact_names(monkeypatch, value, message):
    monkeypatch.setenv("BARDIC_ALLOWED_HOSTS", value)
    with pytest.raises(ValueError, match=message):
        lan.allowed_hosts()


@pytest.mark.parametrize("host, expected", [("0.0.0.0", None), ("192.168.0.160", "192.168.0.160")])
def test_wildcard_bind_follows_the_network_while_a_fixed_bind_is_advertised_as_is(host, expected):
    assert lan.advertised_address(host) == expected


@pytest.mark.parametrize("host", ["127.0.0.1", "::", "localhost"])
def test_a_name_cannot_point_at_an_unreachable_bind(host):
    with pytest.raises(ValueError, match="BARDIC_"):
        lan.advertised_address(host)


def test_named_host_is_trusted_for_reads_and_same_origin_writes(tmp_path, monkeypatch):
    monkeypatch.setenv("BARDIC_LAN_NAME", "bardic")
    with TestClient(create_app(tmp_path), base_url="http://bardic.local:8766") as client:
        assert client.get("/api/books").status_code == 200
        assert client.post("/api/demo", headers={"Origin": "http://bardic.local:8766"}).status_code == 200
        assert client.post("/api/demo", headers={"Origin": "http://evil.example"}).status_code == 403
        assert client.get("/api/books", headers={"Host": "evil.example"}).status_code == 400


def test_name_is_not_trusted_unless_configured(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        assert client.get("/api/books", headers={"Host": "bardic.local:8766"}).status_code == 400


class FakeProcess:
    def __init__(self, argv):
        self.argv, self.returncode = argv, None

    def poll(self):
        return self.returncode

    def stop(self):
        if self.returncode is None:
            self.returncode = -15


def advertiser(addresses=(), **options):
    spawned, messages, remaining = [], [], iter(addresses)

    def spawn(argv):
        spawned.append(FakeProcess(argv))
        return spawned[-1]

    return lan.Advertiser("bardic", 8766, detect=lambda: next(remaining), spawn=spawn,
                          log=messages.append, **options), spawned, messages


def test_name_follows_network_changes_and_is_withdrawn_on_stop():
    names, spawned, messages = advertiser(["192.168.0.160", "192.168.0.160", "10.0.0.5", None, None, "10.0.0.5"])
    names.sync()
    assert spawned[0].argv == ["dns-sd", "-P", "bardic", "_http._tcp", "local", "8766", "bardic.local", "192.168.0.160"]
    names.sync()
    assert len(spawned) == 1 and spawned[0].returncode is None
    names.sync()  # Joined another network.
    assert spawned[0].returncode == -15 and spawned[1].argv[-1] == "10.0.0.5"
    names.sync()  # Offline: nothing to point the name at.
    names.sync()
    assert spawned[1].returncode == -15 and len(spawned) == 2
    names.sync()
    assert len(spawned) == 3
    names.stop()
    assert spawned[2].returncode == -15
    assert messages == ["Advertising http://bardic.local:8766 at 192.168.0.160.",
                        "Advertising http://bardic.local:8766 at 10.0.0.5.",
                        "No network address; bardic.local is paused until this computer rejoins a network.",
                        "Advertising http://bardic.local:8766 at 10.0.0.5."]


def test_fixed_bind_address_is_advertised_without_detection():
    names, spawned, _ = advertiser(address="192.168.0.160")
    names.sync()
    assert spawned[0].argv[-1] == "192.168.0.160"


def test_a_name_owned_elsewhere_is_retried_with_backoff():
    now = [0.0]
    names, spawned, messages = advertiser(address="192.168.0.160", interval=30, clock=lambda: now[0])
    names.sync()
    spawned[0].returncode = 1  # dns-sd: "Name in use, please choose another"
    names.sync()
    assert len(spawned) == 1 and "retrying in 30 s" in messages[-1]
    now[0] = 29
    names.sync()
    assert len(spawned) == 1
    now[0] = 30
    names.sync()
    assert len(spawned) == 2
    spawned[1].returncode = 1
    names.sync()
    assert "retrying in 60 s" in messages[-1]
    now[0] = 91
    names.sync()
    spawned[2].returncode = None
    names.sync()  # Survived an interval: the next failure starts the backoff again.
    assert names.failures == 0


def test_missing_dns_sd_still_serves_without_a_name(monkeypatch):
    monkeypatch.setattr(lan.shutil, "which", lambda name: None)
    names, spawned, messages = advertiser(["192.168.0.160"])
    names.start()
    names.stop()
    assert spawned == [] and "dns-sd is unavailable" in messages[0]


def test_a_failed_spawn_is_reported_without_stopping_the_server():
    messages = []

    def spawn(argv):
        raise OSError("Resource temporarily unavailable")

    names = lan.Advertiser("bardic", 8766, address="192.168.0.160", spawn=spawn, log=messages.append)
    names._sync_safely()
    assert messages[-1] == "Could not advertise bardic.local: Resource temporarily unavailable"


def test_port_probe_sees_a_listening_server():
    with socket.create_server(("127.0.0.1", 0)) as server:
        port = server.getsockname()[1]
        assert lan.port_in_use(port)
    assert not lan.port_in_use(port)


def group_gone(pgid, seconds=5):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.02)
    return False


def wait_until_gone(pid, seconds=5):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.05)
    return False


def pid_written_by(path, seconds=5):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if path.exists() and path.read_text().strip():
            return int(path.read_text())
        time.sleep(0.05)
    raise AssertionError("command did not start")


def test_tied_command_stops_on_request_and_reports_its_own_exit(tmp_path):
    pid_file = tmp_path / "pid"
    tied = lan.TiedProcess(["/bin/sh", "-c", f'echo $$ > "{pid_file}"; exec sleep 60'])
    child = pid_written_by(pid_file)
    assert tied.poll() is None
    tied.stop()
    # 143 is SIGTERM from the pipe watcher; the killpg fallback would give -9 after 5 s.
    assert tied.returncode == 143
    assert wait_until_gone(child) and group_gone(tied.process.pid)
    failed = lan.TiedProcess(["/bin/sh", "-c", "exit 3"])
    failed.process.wait(timeout=5)
    assert failed.returncode == 3  # How a dns-sd name conflict reaches the watchdog.
    assert group_gone(failed.process.pid)  # No helper waits for the next sync.
    failed.stop()


def test_tied_command_does_not_outlive_a_killed_server(tmp_path):
    pid_file = tmp_path / "pid"
    command = ["/bin/sh", "-c", f'echo $$ > "{pid_file}"; exec sleep 60']
    parent = subprocess.Popen([sys.executable, "-c",
                               f"from bardic.lan import TiedProcess; import time; TiedProcess({command!r}); time.sleep(60)"])
    child = pid_written_by(pid_file)
    parent.kill()  # SIGKILL: no finally blocks, no uvicorn shutdown.
    parent.wait()
    assert wait_until_gone(child)
