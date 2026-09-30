"""Start, stop and inspect Bardic servers: the owner's service and development copies.

`./bardicctl` runs this module. The service is a macOS LaunchAgent, so launchd,
not a terminal or agent session, owns the process: it runs the main checkout's
`.venv/bin/python -m bardic` at login, restarts it after a crash and sends
SIGTERM to stop it. That launch reads the checkout's `.env` like any other;
launchd passes no shell settings.

A development server runs the calling checkout's code on its own port with a
scratch library, a loopback bind and blank provider keys. `--keys` passes the
service checkout's provider keys, and nothing else from its `.env`, for a live
test. Each is recorded under ~/.cache/bardic-dev so any session can list or stop it.
"""
import argparse
import fcntl
import json
import os
import plistlib
import re
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

from dotenv import dotenv_values

from .config import PROJECT_ROOT
from .lan import port_in_use

LABEL = "local.bardic"
# launchd's wait for a graceful stop before it sends SIGKILL. Shutdown lets a request already sent finish:
# analysis allows 180 s and ordinary narration 240 s. A long chapter chunk (up to 900 s) is cut off.
EXIT_TIMEOUT = 300
# launchd gives a job 256 open files by default. The server holds one descriptor per connection plus the
# library database and audio files; the Rust replacement caps connections at 128 and needs a 208 budget, and
# both benefit from headroom. These are the soft and hard limits launchd applies to the job.
FILE_LIMIT_SOFT = 1024
FILE_LIMIT_HARD = 2048
READY_TIMEOUT = 90
LOG_ROTATE_BYTES = 10 * 1024 * 1024
DEV_PORTS = range(8770, 8800)
# Blank in development servers: no cloud provider, self-hosted narration server or network exposure.
# `dev start --keys` fills the providers from the service checkout's `.env`; the network settings stay blank.
DEV_PROVIDERS = ("GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "BREEZE_TTS_URL",
                 "BREEZE_API_KEY",
                 # Self-hosted analysis servers: free per request, but they share the owner's GPU.
                 "BARDIC_LOCAL_LLM_URL", "BARDIC_BOOKNLP_URL", "BARDIC_NOVEL_ANALYZER_URL")
DEV_BLANK = (*DEV_PROVIDERS, "BARDIC_LAN_NAME", "BARDIC_HOST", "BARDIC_ALLOWED_HOSTS", "BARDIC_CORS_ORIGINS")
ACTIVE = {"queued", "running"}  # Same as bardic.app.ACTIVE, without importing the application.
BARDIC_COMMAND = re.compile(r"\s-m\s+(bardic|spintails)(\s|$)")
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
LOCAL_HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # Never send loopback probes to a proxy.


class CommandError(Exception):
    """A refusal or failure reported without a traceback."""


# Paths and configuration

def plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def log_path() -> Path:
    return Path.home() / "Library" / "Logs" / "bardic.log"


def dev_home() -> Path:
    return Path(os.environ.get("BARDIC_DEV_HOME") or Path.home() / ".cache" / "bardic-dev").expanduser().resolve()


def main_checkout() -> Path:
    """The repository's main working tree. Worktrees are disposable, so the service never runs from one."""
    try:
        result = subprocess.run(["git", "-C", str(PROJECT_ROOT), "rev-parse", "--path-format=absolute", "--git-common-dir"],
                                capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return PROJECT_ROOT
    return Path(result.stdout.strip()).parent if result.returncode == 0 else PROJECT_ROOT


def linked_worktree() -> bool:
    """Agents work in linked worktrees, where `.git` is a file."""
    return (PROJECT_ROOT / ".git").is_file()


def checkout_settings(checkout: Path) -> dict:
    """What `python -m bardic` in this checkout reads from `.env` when no shell setting overrides it."""
    env_file = checkout / ".env"
    values = dotenv_values(env_file, interpolate=False) if env_file.is_file() else {}

    def setting(name):
        return next((values[key] for key in (f"BARDIC_{name}", f"SPINTAILS_{name}") if values.get(key) is not None), None)

    configured = setting("DATA_DIR")
    if configured is not None:
        library = checkout / configured  # An absolute path replaces the checkout.
    elif not (checkout / ".bardic").exists() and (checkout / ".spintails").is_dir():
        library = checkout / ".spintails"
    else:
        library = checkout / ".bardic"
    host = (values.get("BARDIC_HOST") or "").strip()
    return {"port": int(setting("PORT") or 8765), "library": library,
            "lan_name": (values.get("BARDIC_LAN_NAME") or "").strip().lower().removesuffix(".local") or None,
            # A specific network bind does not answer on loopback.
            "probe_host": host if host and host != "0.0.0.0" else "127.0.0.1"}


def installed_plist() -> dict | None:
    try:
        with plist_path().open("rb") as handle:
            return plistlib.load(handle)
    except FileNotFoundError:
        return None


def service_context() -> tuple[Path, dict, dict | None]:
    """The checkout the service runs (or would run), its settings and the installed job definition."""
    plist = installed_plist()
    checkout = Path(plist["WorkingDirectory"]) if plist else main_checkout()
    return checkout, checkout_settings(checkout), plist


def service_plist(checkout: Path, log: Path) -> dict:
    # launchd's default PATH lacks Homebrew, where ffmpeg usually lives. say and dns-sd are in /usr/bin.
    search = [str(Path(ffmpeg).parent)] if (ffmpeg := shutil.which("ffmpeg")) else []
    search += ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin"]
    return {
        "Label": LABEL,
        # The server itself rather than `uv run`: launchd's SIGKILL after ExitTimeOut must reach Bardic, not a
        # wrapper whose orphaned child would keep the port and library. bardicctl syncs the environment first.
        "ProgramArguments": [str(checkout / ".venv" / "bin" / "python"), "-m", "bardic"],
        "WorkingDirectory": str(checkout),
        "EnvironmentVariables": {"PATH": ":".join(dict.fromkeys(search)), "PYTHONUNBUFFERED": "1"},
        "RunAtLoad": True,
        # Restart after a crash or failed start, not after a requested stop.
        "KeepAlive": {"SuccessfulExit": False},
        "ThrottleInterval": 30,
        "ExitTimeOut": EXIT_TIMEOUT,
        "SoftResourceLimits": {"NumberOfFiles": FILE_LIMIT_SOFT},
        "HardResourceLimits": {"NumberOfFiles": FILE_LIMIT_HARD},
        "StandardOutPath": str(log),
        "StandardErrorPath": str(log),
    }


def overlaps(a: Path, b: Path) -> bool:
    """Whether either directory is or contains the other, by file identity so case and links do not matter."""
    a, b = a.expanduser().resolve(), b.expanduser().resolve()

    def inside(inner: Path, outer: Path) -> bool:
        if not outer.exists():
            return inner == outer or outer in inner.parents
        return any(candidate.exists() and os.path.samefile(candidate, outer) for candidate in (inner, *inner.parents))

    return inside(a, b) or inside(b, a)


# Processes

def _output(argv: list[str]) -> str:
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


def listener(port: int) -> int | None:
    """The process listening on this TCP port, whichever address it bound."""
    pids = _output(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"]).split()
    return int(pids[0]) if pids else None


def command_of(pid: int) -> str:
    return _output(["ps", "-o", "command=", "-p", str(pid)])


def started_at(pid: int) -> str:
    return _output(["ps", "-o", "lstart=", "-p", str(pid)])


def parent_of(pid: int) -> int | None:
    value = _output(["ps", "-o", "ppid=", "-p", str(pid)])
    return int(value) if value.isdigit() else None


def stdout_of(pid: int) -> str | None:
    names = [line[1:] for line in _output(["lsof", "-a", "-p", str(pid), "-d", "1", "-Fn"]).splitlines() if line.startswith("n")]
    return names[0] if names else None


def is_bardic(pid: int) -> bool:
    return bool(BARDIC_COMMAND.search(" " + command_of(pid)))


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    return not _output(["ps", "-o", "stat=", "-p", str(pid)]).startswith("Z")  # An exited, unreaped child is not running.


def ancestry(pid: int) -> list[tuple[int, str]]:
    """Parents up to launchd, to show what (if anything) still owns a server."""
    chain = []
    while (pid := parent_of(pid)) and len(chain) < 10:
        chain.append((pid, command_of(pid) or "?"))
        if pid == 1:
            break
    return chain


def wait_for(condition, timeout: float, interval: float = 0.5) -> bool:
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() >= deadline:
            return False
        time.sleep(interval)
    return True


def probe(host: str, port: int, timeout: float = 5.0) -> list | None:
    """Active jobs when a Bardic server answers, otherwise None. Also the readiness check.

    The Host header is loopback because the server trusts only named hosts, and a server
    bound to one network address would reject that address as a Host.
    """
    request = urllib.request.Request(f"http://{host}:{port}/api/jobs?active=true", headers={"Host": "127.0.0.1"})
    try:
        with LOCAL_HTTP.open(request, timeout=timeout) as response:
            jobs = json.load(response)
    except (OSError, ValueError):
        return None
    return jobs if isinstance(jobs, list) else None


def refuse_active_work(settings: dict, force: bool) -> None:
    if force:
        return
    port = settings["port"]
    jobs = probe(settings["probe_host"], port)
    if jobs is None:
        if listener(port) is not None:
            raise CommandError(f"Could not read active jobs from the server on port {port}. "
                               "Check ./bardicctl status, or pass --force to stop it anyway.")
        return
    active = [job for job in jobs if job.get("status") in ACTIVE]  # An older server ignores ?active=true.
    if active:
        kinds = ", ".join(sorted({f"{job.get('kind', 'job')} {job['status']}" for job in active}))
        raise CommandError(f"{len(active)} job(s) are active ({kinds}). Stop them in the app, or pass --force. "
                           "Shutdown waits for a safe boundary; requests already sent can still be charged, "
                           "and unfinished jobs are marked interrupted.")


def require_consent(args) -> None:
    if linked_worktree() and not args.yes:
        raise CommandError("This command interrupts the owner's Bardic service, and this is a linked worktree. "
                           "Pass --yes only if the user asked for it. To run your change, use ./bardicctl dev start.")


def log_tail(path: Path, lines: int = 20) -> str:
    try:
        with path.open("rb") as handle:
            handle.seek(max(0, handle.seek(0, os.SEEK_END) - 16384))
            return "\n".join(handle.read().decode(errors="replace").splitlines()[-lines:])
    except OSError:
        return "(no log)"


def rotate_log(path: Path) -> None:
    """launchd opens the log when it starts the job; keep one previous file once it grows large."""
    try:
        if path.stat().st_size > LOG_ROTATE_BYTES:
            path.replace(path.with_name(path.name + ".1"))
    except FileNotFoundError:
        pass


# launchd

def require_macos() -> None:
    if sys.platform != "darwin":
        raise CommandError("The service uses macOS launchd. Elsewhere, run `uv run --frozen python -m bardic` "
                           "under your own supervisor, such as systemd --user.")


def target() -> str:
    return f"gui/{os.getuid()}/{LABEL}"


def launchctl(*args: str, timeout: float = 30) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True, timeout=timeout)


def service_state() -> dict | None:
    """launchd's view of the job, or None when it is not loaded."""
    result = launchctl("print", target())
    if result.returncode != 0:
        return None

    def field(name):  # Top-level fields have one tab; nested sections repeat names deeper.
        match = re.search(rf"^\t{name} = (.+)$", result.stdout, re.M)
        return match.group(1).strip() if match else None

    pid = field("pid")
    return {"state": field("state"), "pid": int(pid) if pid and pid.isdigit() else None, "last_exit": field("last exit code")}


def checked(result: subprocess.CompletedProcess, action: str) -> None:
    if result.returncode != 0:
        raise CommandError(f"launchctl {action} failed: {(result.stderr or result.stdout).strip()}")


def sync_environment(checkout: Path) -> None:
    """Install the locked dependencies before anything is stopped, so a failure leaves the service as it was."""
    uv = shutil.which("uv") or os.environ.get("UV")
    if not uv:
        raise CommandError("uv was not found on PATH.")
    env = {key: value for key, value in os.environ.items() if key != "VIRTUAL_ENV"}  # Set for this checkout by `uv run`.
    if subprocess.run([uv, "sync", "--frozen", "--quiet"], cwd=checkout, env=env).returncode != 0:
        raise CommandError(f"`uv sync --frozen` failed in {checkout}; nothing was stopped.")
    if not (checkout / ".venv" / "bin" / "python").exists():
        raise CommandError(f"{checkout / '.venv' / 'bin' / 'python'} is missing after uv sync.")


def bootstrap() -> None:
    rotate_log(log_path())
    checked(launchctl("bootstrap", f"gui/{os.getuid()}", str(plist_path())), "bootstrap")


def refuse_unmanaged_listener(port: int, checkout: Path) -> None:
    """A second server would fail its port check, and launchd would keep retrying it."""
    pid = listener(port)
    if pid is None:
        return
    if is_bardic(pid):
        raise CommandError(f"A Bardic server outside launchd (pid {pid}) already serves port {port}. "
                           "Run ./bardicctl stop first.")
    raise CommandError(f"Port {port} is in use by pid {pid}: {command_of(pid)}. "
                       f"Stop it or change BARDIC_PORT in {checkout / '.env'}.")


def await_ready(settings: dict) -> None:
    def serving():  # The job's own process holds the port, and it answers.
        state = service_state()
        return bool(state and state["pid"]) and listener(settings["port"]) == state["pid"] \
            and probe(settings["probe_host"], settings["port"]) is not None

    if not wait_for(serving, READY_TIMEOUT):
        state = service_state() or {}
        raise CommandError(f"The service did not answer on port {settings['port']} within {READY_TIMEOUT}s "
                           f"(launchd state {state.get('state')}, last exit {state.get('last_exit')}). "
                           f"Log tail:\n{log_tail(log_path())}")


def stop_service() -> None:
    pid = (service_state() or {}).get("pid")
    result = launchctl("bootout", target())
    if result.returncode != 0 and service_state() is not None:
        checked(result, "bootout")
    print("Stopping; shutdown waits for work in progress to reach a safe boundary...")
    # bootout returns at once. The server may still be finishing a request and holding the port and library.
    if not wait_for(lambda: service_state() is None and not (pid and alive(pid)), EXIT_TIMEOUT + 15):
        raise CommandError(f"The service (pid {pid}) has not finished stopping. Check ./bardicctl status.")


def urls(settings: dict) -> str:
    port = settings["port"]
    names = [f"http://127.0.0.1:{port}"] + ([f"http://{settings['lan_name']}.local:{port}"] if settings["lan_name"] else [])
    return "  ".join(names)


# Service commands

def cmd_install(args) -> None:
    require_macos()
    require_consent(args)
    checkout = Path(args.checkout).expanduser().resolve() if args.checkout else main_checkout()
    if not (checkout / "bardic" / "__main__.py").is_file():
        raise CommandError(f"{checkout} is not a Bardic checkout.")
    if (checkout / ".git").is_file():
        raise CommandError(f"{checkout} is a linked worktree, which can be removed at any time. "
                           "Install from the main checkout.")
    if service_state() is not None:  # Reloading: check the server running now, not the new definition.
        refuse_active_work(service_context()[1], args.force)
    sync_environment(checkout)
    if service_state() is not None:
        stop_service()
    settings = checkout_settings(checkout)
    refuse_unmanaged_listener(settings["port"], checkout)
    plist_path().parent.mkdir(parents=True, exist_ok=True)
    log_path().parent.mkdir(parents=True, exist_ok=True)
    with plist_path().open("wb") as handle:
        plistlib.dump(service_plist(checkout, log_path()), handle)
    bootstrap()
    print(f"Installed {plist_path()}; launchd starts Bardic from {checkout} at login.")
    await_ready(settings)
    print(f"Running: {urls(settings)}")


def cmd_uninstall(args) -> None:
    require_macos()
    require_consent(args)
    _, settings, _ = service_context()
    if service_state() is not None:
        refuse_active_work(settings, args.force)
        stop_service()
    plist_path().unlink(missing_ok=True)
    print("Removed the LaunchAgent. Bardic no longer starts at login; ./bardicctl install restores it.")


def cmd_start(args) -> None:
    require_macos()
    checkout, settings, plist = service_context()
    if plist is None:
        raise CommandError("The service is not installed. Run ./bardicctl install (it uses the main checkout).")
    state = service_state()
    if state and state["state"] == "running":
        print(f"Already running (pid {state['pid']}): {urls(settings)}")
        return
    refuse_unmanaged_listener(settings["port"], checkout)
    sync_environment(checkout)
    if state is None:
        bootstrap()
    else:
        rotate_log(log_path())
        checked(launchctl("kickstart", target()), "kickstart")
    await_ready(settings)
    print(f"Running: {urls(settings)}")


def cmd_stop(args) -> None:
    require_consent(args)
    _, settings, _ = service_context()
    port = settings["port"]
    if sys.platform == "darwin" and service_state() is not None:
        refuse_active_work(settings, args.force)
        stop_service()
        print("Stopped. launchd starts it again at login; ./bardicctl uninstall prevents that.")
        return
    pid = listener(port)
    if pid is None:
        print(f"No server is listening on port {port}.")
        return
    if not is_bardic(pid):
        raise CommandError(f"Port {port} is served by pid {pid}, which is not Bardic: {command_of(pid)}. Not stopping it.")
    refuse_active_work(settings, args.force)
    os.kill(pid, signal.SIGTERM)
    print(f"Sent SIGTERM to Bardic pid {pid} (not managed by launchd); waiting for work in progress to reach a safe boundary...")
    if not wait_for(lambda: not alive(pid), EXIT_TIMEOUT):
        raise CommandError(f"pid {pid} is still shutting down after {EXIT_TIMEOUT}s. "
                           f"Check its log; use kill -KILL {pid} only if it is stuck.")
    print("Stopped.")


def cmd_restart(args) -> None:
    require_macos()
    require_consent(args)
    checkout, settings, plist = service_context()
    state = service_state()
    if state is None:
        pid = listener(settings["port"])
        if plist is None and pid and is_bardic(pid):
            raise CommandError(f"The Bardic server on port {settings['port']} (pid {pid}) is not managed by launchd. "
                               "Run ./bardicctl stop, then ./bardicctl install.")
        cmd_start(args)
        return
    if plist is None:
        raise CommandError(f"launchd has {LABEL} loaded but {plist_path()} is missing. Run ./bardicctl install.")
    refuse_active_work(settings, args.force)
    sync_environment(checkout)
    # Unload and load rather than `kickstart -k`: launchd delays a respawn within ThrottleInterval of the
    # previous launch, and loading again also applies an edited job definition.
    stop_service()
    bootstrap()
    await_ready(settings)
    print(f"Running from {checkout}: {urls(settings)}")


def git_head(checkout: Path) -> str:
    branch = _output(["git", "-C", str(checkout), "rev-parse", "--abbrev-ref", "HEAD"])
    commit = _output(["git", "-C", str(checkout), "rev-parse", "--short", "HEAD"])
    return f"{branch} @ {commit}" if commit else "not a git checkout"


def cmd_status(args) -> None:
    checkout, settings, plist = service_context()
    port = settings["port"]
    state = service_state() if sys.platform == "darwin" else None
    if plist is None:
        job = "not installed (./bardicctl install)"
    elif state is None:
        job = "installed, not loaded (./bardicctl start)"
    elif state["state"] == "running":
        job = f"running, pid {state['pid']}"
    else:
        job = f"loaded, {state['state']}, last exit code {state['last_exit']} (./bardicctl logs)"
    print(f"Bardic service ({LABEL})\n  launchd   {job}")

    pid = listener(port)
    if pid is None:
        print(f"  port      {port}: nothing listening")
    elif not is_bardic(pid):
        print(f"  port      {port}: pid {pid}, not Bardic: {command_of(pid)}")
    else:
        chain = ancestry(pid)
        managed = state is not None and state["pid"] in (pid, *(parent for parent, _ in chain[:1]))
        print(f"  port      {port}: Bardic pid {pid}, started {started_at(pid)}")
        if managed:
            print("  owner     launchd")
        else:
            parents = " <- ".join(f"{Path(command.split()[0]).name} ({parent})" for parent, command in chain)
            detached = chain and chain[-1][0] == 1 and all(BARDIC_COMMAND.search(" " + command) for _, command in chain[:-1])
            print(f"  owner     not launchd; parents: {parents}")
            if detached:
                print("            detached: whatever launched it has exited, so no terminal or agent session owns it.")
            print("            To manage it: ./bardicctl stop, then ./bardicctl install")
        jobs = probe(settings["probe_host"], port)
        active = [job for job in jobs or [] if job.get("status") in ACTIVE]
        print(f"  jobs      {len(active)} active" if jobs is not None else "  jobs      unknown (the server did not answer)")
    print(f"  checkout  {checkout} ({git_head(checkout)})")
    print(f"  library   {settings['library']}")
    print(f"  urls      {urls(settings)}")
    print(f"  log       {(stdout_of(pid) if pid else None) or log_path()}")
    running = [record for record in dev_records() if dev_running(record)]
    if running:
        print(f"Development servers: {len(running)} running (./bardicctl dev list)")


def cmd_logs(args) -> None:
    path = Path(args.path) if getattr(args, "path", None) else log_path()
    if not path.exists():
        raise CommandError(f"No log at {path}.")
    sys.stdout.flush()
    os.execvp("tail", ["tail", "-n", str(args.lines), *(["-F"] if args.follow else []), str(path)])


# Development servers

def dev_name(value: str | None) -> str:
    name = value or PROJECT_ROOT.name
    if not NAME.fullmatch(name):
        raise CommandError(f"Use a name of letters, digits, dot, dash or underscore, not {name!r}.")
    return name


def dev_record(name: str) -> dict | None:
    try:
        return json.loads((dev_home() / name / "instance.json").read_text())
    except (OSError, ValueError):
        return None


def dev_records() -> list[dict]:
    home = dev_home()
    names = sorted(path.parent.name for path in home.glob("*/instance.json")) if home.is_dir() else []
    return [record for name in names if (record := dev_record(name))]


def dev_running(record: dict) -> bool:
    """The recorded process still exists and is the one started, not a reused process ID."""
    pid = record.get("pid")
    return bool(pid) and alive(pid) and started_at(pid) == record.get("started") and is_bardic(pid)


def own_record(args, name: str) -> dict | None:
    """The default name is the directory name, which another checkout may share; acting on its server needs --name."""
    record = dev_record(name)
    if record and not args.name and record.get("checkout") != str(PROJECT_ROOT):
        raise CommandError(f"The development server {name!r} belongs to {record.get('checkout')}. "
                           f"Pass --name {name} to act on it, or another --name for this checkout.")
    return record


def dev_environment(base: dict, port: int, library: Path, providers: dict | None = None) -> dict:
    """Empty shell values beat `.env`, so a development server stays on loopback and keyless unless given providers."""
    env = {**base, **{name: "" for name in DEV_BLANK}, **(providers or {}),
           "BARDIC_PORT": str(port), "BARDIC_DATA_DIR": str(library), "PYTHONUNBUFFERED": "1"}
    for alias in ("SPINTAILS_PORT", "SPINTAILS_DATA_DIR"):
        env.pop(alias, None)
    return env


def provider_settings(checkout: Path) -> dict:
    """The provider keys and Breeze server in the service checkout's `.env`, for `dev start --keys`.

    Only that file counts: a shell ANTHROPIC_API_KEY may belong to the calling agent rather than to Bardic.
    """
    env_file = checkout / ".env"
    values = dotenv_values(env_file, interpolate=False) if env_file.is_file() else {}
    providers = {name: values[name].strip() for name in DEV_PROVIDERS if (values.get(name) or "").strip()}
    if not providers:
        raise CommandError(f"--keys found no provider keys in {env_file}.")
    return providers


def dev_port(requested: int | None, previous: int | None, name: str, service_port: int) -> int:
    if requested:
        if requested == service_port:
            raise CommandError(f"Port {requested} belongs to the service, even while it is stopped.")
        if port_in_use(requested):
            raise CommandError(f"Port {requested} is already in use.")
        return requested
    claimed = {record.get("port") for record in dev_records() if record.get("name") != name and dev_running(record)}
    # Reusing the previous port keeps the browser origin, and with it saved reading positions.
    for port in ([previous] if previous else []) + list(DEV_PORTS):
        if port not in claimed and port != service_port and not port_in_use(port):
            return port
    raise CommandError(f"No free development port in {DEV_PORTS.start}-{DEV_PORTS.stop - 1}. "
                       "Stop one with ./bardicctl dev stop --name NAME.")


def cmd_dev_start(args) -> None:
    name = dev_name(args.name)
    dev_home().mkdir(parents=True, exist_ok=True)
    # One start at a time, from check to bound port: concurrent sessions would otherwise pick the same
    # port, or overwrite each other's record and orphan the server that did start.
    with (dev_home() / ".start.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        start_dev(args, name)


def start_dev(args, name: str) -> None:
    directory = dev_home() / name
    previous = own_record(args, name)
    if previous and dev_running(previous):
        if previous.get("checkout") != str(PROJECT_ROOT):
            raise CommandError(f"{name} is running from {previous['checkout']}. Stop it first or choose another --name.")
        print(f"{name} is already running (pid {previous['pid']}): http://127.0.0.1:{previous['port']}")
        if args.keys != bool(previous.get("keys")):
            print(f"Its keys are {'live' if previous.get('keys') else 'blank'}; "
                  f"use ./bardicctl dev restart{' --keys' if args.keys else ''} to change that.")
        return
    library = (Path(args.library).expanduser().resolve() if args.library
               else Path(previous["library"]) if previous and previous.get("library") else directory / "library")
    service = checkout_settings(service_context()[0])
    if overlaps(library, service["library"]):
        raise CommandError(f"{library} overlaps the service's library {service['library']}. "
                           "Test against a copy restored from a backup.")
    port = dev_port(args.port, (previous or {}).get("port"), name, service["port"])
    env_file = service_context()[0] / ".env"
    providers = provider_settings(env_file.parent) if args.keys else {}
    directory.mkdir(parents=True, exist_ok=True)
    library.mkdir(parents=True, exist_ok=True)
    log = directory / "server.log"
    with log.open("ab") as output:
        output.write(f"\n==== {datetime.now().isoformat(timespec='seconds')} {PROJECT_ROOT} on port {port}\n".encode())
        output.flush()
        process = subprocess.Popen([sys.executable, "-m", "bardic"], cwd=PROJECT_ROOT,
                                   env=dev_environment(os.environ, port, library, providers), stdin=subprocess.DEVNULL,
                                   stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
    record = {"name": name, "pid": process.pid, "started": started_at(process.pid), "port": port,
              "checkout": str(PROJECT_ROOT), "library": str(library), "log": str(log), "keys": sorted(providers)}
    (directory / "instance.json").write_text(json.dumps(record, indent=2) + "\n")
    lsof = shutil.which("lsof")

    def serving():  # Our process holds the port and answers, not some other server that took it first.
        return probe("127.0.0.1", port) is not None and (lsof is None or listener(port) == process.pid)

    wait_for(lambda: process.poll() is not None or serving(), READY_TIMEOUT)
    if process.poll() is not None or not serving():
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                process.kill()
        raise CommandError(f"{name} did not start (exit {process.poll()}). Log tail:\n{log_tail(log)}")
    keys = f"{', '.join(sorted(providers))} from {env_file} (real, possibly billed requests)" if providers else "blank"
    print(f"{name}: http://127.0.0.1:{port}  (pid {process.pid})\n  checkout  {PROJECT_ROOT}\n"
          f"  library   {library}\n  keys      {keys}\n  log       {log}\n"
          f"Stop it with ./bardicctl dev stop --name {name}")


def stop_dev(record: dict) -> None:
    if not dev_running(record):
        print(f"{record['name']}: not running")
        return
    os.kill(record["pid"], signal.SIGTERM)
    if not wait_for(lambda: not alive(record["pid"]), 60):
        raise CommandError(f"{record['name']} (pid {record['pid']}) is still shutting down; see {record['log']}.")
    print(f"{record['name']}: stopped. Its library {record['library']} and log {record['log']} remain.")


def cmd_dev_stop(args) -> None:
    if args.all:
        records = [record for record in dev_records() if dev_running(record)]
        if not records:
            print("No development servers are running.")
    else:
        name = dev_name(args.name)
        records = [own_record(args, name) or {"name": name}]
    for record in records:
        stop_dev(record)


def cmd_dev_restart(args) -> None:
    record = own_record(args, dev_name(args.name))
    if record:
        stop_dev(record)
    cmd_dev_start(args)


def cmd_dev_list(args) -> None:
    records = dev_records()
    if not records:
        print("No development servers recorded.")
        return
    print(f"{'NAME':<32} {'STATE':<8} {'PORT':<5} {'PID':<7} {'KEYS':<5} CHECKOUT")
    for record in records:
        running = dev_running(record)
        checkout = record.get("checkout", "?") + ("" if Path(record.get("checkout", "")).is_dir() else " (removed)")
        print(f"{record['name']:<32} {'running' if running else 'stopped':<8} {record.get('port', ''):<5} "
              f"{record.get('pid', '') if running else '':<7} {'live' if record.get('keys') else 'blank':<5} {checkout}")


def cmd_dev_logs(args) -> None:
    record = dev_record(dev_name(args.name))
    if record is None:
        raise CommandError(f"No development server named {dev_name(args.name)}.")
    args.path = record["log"]
    cmd_logs(args)


def parser() -> argparse.ArgumentParser:
    top = argparse.ArgumentParser(prog="bardicctl", description=__doc__.split("\n\n")[0])
    commands = top.add_subparsers(dest="command", required=True, metavar="COMMAND")

    def command(group, name, handler, summary, interrupts=False):
        sub = group.add_parser(name, help=summary, description=summary)
        sub.set_defaults(handler=handler)
        if interrupts:
            sub.add_argument("--force", action="store_true", help="proceed even while jobs are active")
            sub.add_argument("--yes", action="store_true", help="confirm from a linked worktree that the user asked for this")
        return sub

    command(commands, "status", cmd_status, "show what serves the configured port and who owns it")
    command(commands, "start", cmd_start, "start the installed service")
    command(commands, "stop", cmd_stop, "stop the service, or a Bardic server outside launchd on its port", interrupts=True)
    command(commands, "restart", cmd_restart, "restart the service to load code and .env changes", interrupts=True)
    install = command(commands, "install", cmd_install, "run Bardic under launchd from the main checkout, at login", interrupts=True)
    install.add_argument("--checkout", help="checkout to run (default: this repository's main working tree)")
    command(commands, "uninstall", cmd_uninstall, "stop the service and remove its LaunchAgent", interrupts=True)
    logs = command(commands, "logs", cmd_logs, "show the service log")
    logs.add_argument("-n", "--lines", type=int, default=40)
    logs.add_argument("-f", "--follow", action="store_true")

    dev = commands.add_parser("dev", help="isolated development servers for testing a checkout",
                              description="Isolated servers running this checkout: own port, scratch library, "
                                          "loopback only, provider keys blank unless --keys.")
    dev_commands = dev.add_subparsers(dest="dev_command", required=True, metavar="COMMAND")
    for name, handler, summary in (("start", cmd_dev_start, "start this checkout's development server"),
                                   ("restart", cmd_dev_restart, "restart it after Python edits, keeping its port and library")):
        sub = command(dev_commands, name, handler, summary)
        sub.add_argument("--name", help="instance name (default: this checkout's directory name)")
        sub.add_argument("--port", type=int, help=f"port (default: previous or first free in {DEV_PORTS.start}-{DEV_PORTS.stop - 1})")
        sub.add_argument("--library", help="data directory, e.g. a restored copy (default: previous or a scratch library)")
        sub.add_argument("--keys", action="store_true",
                         help="use the provider keys and Breeze URL in the service checkout's .env for a live test "
                              "(real, possibly billed requests; not kept by a restart without --keys)")
    stop = command(dev_commands, "stop", cmd_dev_stop, "stop a development server")
    stop.add_argument("--name", help="instance name (default: this checkout's directory name)")
    stop.add_argument("--all", action="store_true", help="stop every running development server")
    command(dev_commands, "list", cmd_dev_list, "list development servers from every checkout")
    dev_logs = command(dev_commands, "logs", cmd_dev_logs, "show a development server's log")
    dev_logs.add_argument("--name", help="instance name (default: this checkout's directory name)")
    dev_logs.add_argument("-n", "--lines", type=int, default=40)
    dev_logs.add_argument("-f", "--follow", action="store_true")
    return top


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        args.handler(args)
    except CommandError as error:
        print(f"bardicctl: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
