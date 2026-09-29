"""Start, wait for and stop the server under test.

Spawn mode runs a command with a fresh data directory, a free loopback port and no provider keys,
in its own process group, and always terminates that group (and only that group) when the session
ends: normally, on a failed test, on Ctrl-C, on SIGTERM, and through ``atexit`` as a last resort.
POSIX only.
"""
from __future__ import annotations

import atexit
import ipaddress
import os
import shlex
import signal
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import httpx

OWNER_PORT = 8765
REPO_ROOT = Path(__file__).resolve().parent.parent

# Blank so that nothing in the caller's environment, or a project ``.env``, reaches a provider.
# Values already present in the process environment win over a ``.env`` file, so blank is enough.
BLANKED_ENVIRONMENT = (
    'GEMINI_API_KEY', 'GOOGLE_API_KEY', 'OPENAI_API_KEY', 'ANTHROPIC_API_KEY',
    'BREEZE_TTS_URL', 'BREEZE_API_KEY',
    'BARDIC_LOCAL_LLM_URL', 'BARDIC_BOOKNLP_URL', 'BARDIC_NOVEL_ANALYZER_URL',
    'BARDIC_LAN_NAME', 'BARDIC_ALLOWED_HOSTS', 'BARDIC_CORS_ORIGINS',
)


class RefusedTarget(Exception):
    """The requested base URL is not one the harness may drive."""


def check_target(base_url: str, *, allow_remote: bool, allow_owner_port: bool) -> str:
    """Validate a ``--base-url``; return it without a trailing slash."""
    parts = urlsplit(base_url)
    if parts.scheme not in ('http', 'https') or not parts.hostname:
        raise RefusedTarget(f'--base-url must look like http://127.0.0.1:PORT (got {base_url!r})')
    if parts.path not in ('', '/') or parts.query or parts.fragment:
        raise RefusedTarget(f'--base-url must be a server root without a path (got {base_url!r})')
    host = parts.hostname
    try:
        loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = host.lower() == 'localhost'
    if not loopback and not allow_remote:
        raise RefusedTarget(f'{host} is not a loopback address. The suite imports books and changes settings; '
                            'pass --allow-remote only for a server you own and can discard.')
    port = parts.port or (443 if parts.scheme == 'https' else 80)
    if port == OWNER_PORT and not allow_owner_port:
        raise RefusedTarget(f'port {OWNER_PORT} is where the owner\'s Bardic service normally listens. The suite would '
                            'write to that library. Use a scratch server, or pass --allow-owner-port if you are sure.')
    return base_url.rstrip('/')


def free_port() -> int:
    """A port the OS reports free on loopback (never the owner's port)."""
    while True:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        if port != OWNER_PORT:
            return port


def default_command() -> list[str]:
    """This repository's Python server.

    The current interpreter when it has the server's dependencies (the usual case under
    ``uv run``), otherwise ``uv run --frozen``. Both run ``python -m bardic`` from the repo root.
    """
    import importlib.util
    if importlib.util.find_spec('uvicorn') and importlib.util.find_spec('fastapi'):
        return [sys.executable, '-m', 'bardic']
    return ['uv', 'run', '--frozen', 'python', '-m', 'bardic']


_LIVE: list[subprocess.Popen] = []


def _terminate(process: subprocess.Popen, grace: float = 15.0) -> None:
    """Stop the child's process group: SIGTERM, then SIGKILL. Never signals anything else."""
    group = process.pid  # start_new_session made the child its own group leader
    if group == os.getpgid(0):
        raise RuntimeError('refusing to signal the test process\'s own process group')
    for sig, wait in ((signal.SIGTERM, grace), (signal.SIGKILL, 5.0)):
        if process.poll() is not None and not _group_alive(process):
            return
        try:
            os.killpg(group, sig)
        except ProcessLookupError:
            break
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            if process.poll() is not None and not _group_alive(process):
                return
            time.sleep(0.05)
    process.poll()


def _group_alive(process: subprocess.Popen) -> bool:
    try:
        os.killpg(process.pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return True


def _kill_everything() -> None:
    for process in list(_LIVE):
        _terminate(process, grace=5.0)
        _LIVE.remove(process)


atexit.register(_kill_everything)


def _install_signal_handlers() -> None:
    """Make SIGTERM/SIGHUP to the test process stop the child too, then die as usual."""
    def handler(signum, frame):
        _kill_everything()
        signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)
    for name in ('SIGTERM', 'SIGHUP'):
        try:
            signal.signal(getattr(signal, name), handler)
        except (ValueError, OSError):  # not the main thread, or unsupported
            pass


@dataclass
class Server:
    base_url: str
    spawned: bool
    command: list[str] | None = None
    data_dir: Path | None = None
    log_path: Path | None = None
    process: subprocess.Popen | None = None

    def stop(self) -> None:
        if self.process is not None:
            _terminate(self.process)
            if self.process in _LIVE:
                _LIVE.remove(self.process)

    def log_tail(self, lines: int = 40) -> str:
        if self.log_path is None or not self.log_path.exists():
            return '(no server log)'
        text = self.log_path.read_text(errors='replace').splitlines()
        return '\n'.join(text[-lines:])


def spawn(command: list[str], *, data_dir: Path, log_path: Path, cwd: Path = REPO_ROOT,
          extra_env: dict[str, str] | None = None, timeout: float = 60.0) -> Server:
    """Run ``command`` as a server on a free loopback port and wait until ``/api/status`` answers 200.

    ``{port}``, ``{host}`` and ``{data_dir}`` in the command are replaced. The environment is the
    current one with ``BARDIC_DATA_DIR``, ``BARDIC_PORT``, ``BARDIC_HOST=127.0.0.1`` set and provider
    keys, service URLs and network settings blank.
    """
    port = free_port()
    values = {'port': str(port), 'host': '127.0.0.1', 'data_dir': str(data_dir)}
    argv = []
    for part in command:
        for name, value in values.items():
            part = part.replace('{' + name + '}', value)
        argv.append(part)
    env = dict(os.environ)
    env.update({name: '' for name in BLANKED_ENVIRONMENT})
    env.update({'BARDIC_DATA_DIR': str(data_dir), 'BARDIC_PORT': str(port), 'BARDIC_HOST': '127.0.0.1'})
    env.update(extra_env or {})
    data_dir.mkdir(parents=True, exist_ok=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    _install_signal_handlers()
    log = log_path.open('wb')
    try:
        process = subprocess.Popen(argv, cwd=str(cwd), env=env, stdin=subprocess.DEVNULL, stdout=log,
                                   stderr=subprocess.STDOUT, start_new_session=True)
    except OSError as error:
        log.close()
        raise RuntimeError(f'could not start {shlex.join(argv)}: {error}') from error
    finally:
        log.close()
    _LIVE.append(process)
    server = Server(f'http://127.0.0.1:{port}', True, argv, data_dir, log_path, process)
    try:
        _wait_ready(server, timeout)
    except BaseException:
        server.stop()
        raise
    return server


def _wait_ready(server: Server, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    last = 'no answer yet'
    with httpx.Client(timeout=3.0) as client:
        while time.monotonic() < deadline:
            if server.process.poll() is not None:
                raise RuntimeError(f'the server exited with status {server.process.returncode} before answering '
                                   f'GET /api/status.\ncommand: {shlex.join(server.command)}\n--- server log ---\n'
                                   + server.log_tail())
            try:
                response = client.get(f'{server.base_url}/api/status')
                if response.status_code == 200:
                    return
                last = f'GET /api/status answered {response.status_code}'
            except httpx.HTTPError as error:
                last = f'{type(error).__name__}: {error}'
            time.sleep(0.2)
    raise RuntimeError(f'the server did not answer GET /api/status with 200 within {timeout:.0f}s ({last}).\n'
                       f'command: {shlex.join(server.command)}\n--- server log ---\n' + server.log_tail())
