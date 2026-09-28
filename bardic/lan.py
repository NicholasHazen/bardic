"""Opt-in local-network access for `python -m bardic`.

Loopback remains the default. Setting BARDIC_LAN_NAME binds every IPv4
interface, trusts `<name>.local` as a Host header and asks macOS's
mDNSResponder, through `dns-sd -P`, to answer `<name>.local` with this
computer's current network address. There is no authentication: any device
that can reach the port can use the library and the configured provider keys.
"""
import ipaddress
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time

LOOPBACK = "127.0.0.1"
ALL_IPV4 = "0.0.0.0"
LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
MAX_RETRY_SECONDS = 900
_UNSET = object()


def lan_name() -> str | None:
    """The configured single-label name, without its `.local` suffix."""
    value = os.environ.get("BARDIC_LAN_NAME", "").strip().lower().removesuffix(".local")
    if not value:
        return None
    if not LABEL.fullmatch(value):
        raise ValueError("BARDIC_LAN_NAME must be one name such as 'bardic': letters, digits and inner hyphens")
    return value


def bind_host() -> str:
    return os.environ.get("BARDIC_HOST", "").strip() or (ALL_IPV4 if lan_name() else LOOPBACK)


def allowed_hosts() -> list[str]:
    """Host headers accepted beyond loopback. Explicit names keep DNS-rebinding protection."""
    hosts = [f"{name}.local"] if (name := lan_name()) else []
    for entry in os.environ.get("BARDIC_ALLOWED_HOSTS", "").split(","):
        entry = entry.strip().lower()
        if not entry:
            continue
        if "*" in entry:
            raise ValueError("BARDIC_ALLOWED_HOSTS takes explicit names; a wildcard would disable DNS-rebinding protection")
        if "/" in entry or (":" in entry and not entry.startswith("[")):
            raise ValueError(f"BARDIC_ALLOWED_HOSTS entries are names or addresses without a scheme or port: {entry!r}")
        hosts.append(entry)
    return hosts


def advertised_address(host: str) -> str | None:
    """The bind address to advertise, or None to follow the current network address."""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        raise ValueError(f"BARDIC_LAN_NAME needs BARDIC_HOST to be an IPv4 address, not {host!r}") from None
    if address.version != 4 or address.is_loopback:
        raise ValueError("BARDIC_LAN_NAME needs a network-reachable IPv4 bind; unset BARDIC_HOST or set it to 0.0.0.0")
    return None if address.is_unspecified else str(address)


def current_lan_address() -> str | None:
    """This computer's IPv4 address on its default route. UDP connect selects a route without sending."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 9))
            address = probe.getsockname()[0]
    except OSError:
        return None
    return None if address == ALL_IPV4 or ipaddress.ip_address(address).is_loopback else address


def port_in_use(port: int) -> bool:
    """Whether a server already answers on this computer's loopback port.

    uvicorn sets SO_REUSEADDR, and macOS then lets 0.0.0.0:PORT and
    127.0.0.1:PORT bind together; loopback traffic would silently reach
    whichever server bound the specific address.
    """
    try:
        with socket.create_connection((LOOPBACK, port), timeout=0.5):
            return True
    except OSError:
        return False


class TiedProcess:
    """Run a command that ends when Bardic does, even if Bardic is killed.

    macOS has no parent-death signal. The shell holds a pipe whose only writer
    is this process, so end of file from any kind of exit stops the command.
    The shell exits with the command's own status, which reports name conflicts,
    after clearing its helpers from its own process group. Its later stderr is
    discarded so a requested stop does not print a job-termination notice.
    """

    SCRIPT = ('exec 3<&0; "$@" 3<&- & child=$!; { cat <&3 >/dev/null; kill "$child" 2>/dev/null; } & '
              'exec 3<&- 2>/dev/null; wait "$child"; status=$?; trap "" TERM; kill 0; exit "$status"')

    def __init__(self, argv: list[str]):
        self.process = subprocess.Popen(["/bin/sh", "-c", self.SCRIPT, "sh", *argv],
                                        stdin=subprocess.PIPE, start_new_session=True)

    @property
    def returncode(self):
        return self.process.returncode

    def poll(self):
        return self.process.poll()

    def stop(self) -> None:
        self.process.stdin.close()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(self.process.pid, signal.SIGKILL)
            self.process.wait()


def _log(message: str) -> None:
    print(f"Bardic: {message}", file=sys.stderr, flush=True)


class Advertiser:
    """Keep `<name>.local` pointed at this computer while the server runs.

    mDNSResponder answers for a proxy name only while its `dns-sd -P` process
    lives, so stopping the server withdraws the name. The address is rechecked
    because a laptop can change networks while Bardic keeps running. dns-sd
    exits when another device already owns the name; retries then back off.
    """

    def __init__(self, name: str, port: int, address: str | None = None, interval: float = 30,
                 detect=current_lan_address, spawn=TiedProcess, log=_log, clock=time.monotonic):
        self.name, self.port, self.fixed, self.interval = name, port, address, interval
        self.detect, self.spawn, self.log, self.clock = detect, spawn, log, clock
        self.process = None
        self.address = _UNSET
        self.failures = 0
        self.retry_at = 0.0
        self.stopping = threading.Event()
        self.thread = None

    def start(self) -> None:
        if sys.platform != "darwin" or shutil.which("dns-sd") is None:
            self.log(f"macOS dns-sd is unavailable, so {self.name}.local is not advertised. "
                     "Other devices can still use this computer's own address.")
            return
        self._sync_safely()
        self.thread = threading.Thread(target=self._watch, name="bardic-lan-name", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stopping.set()
        if self.thread is not None:
            self.thread.join()
        self._withdraw()

    def _watch(self) -> None:
        while not self.stopping.wait(self.interval):
            self._sync_safely()

    def _sync_safely(self) -> None:
        # Advertising is an extra; a failed spawn must not stop serving or end the watch.
        try:
            self.sync()
        except OSError as exc:
            self.log(f"Could not advertise {self.name}.local: {exc}")

    def sync(self) -> None:
        address = self.fixed or self.detect()
        if address == self.address:
            if self.process is not None:
                if self.process.poll() is None:
                    self.failures = 0
                    return
                self.failures += 1
                delay = min(self.interval * 2 ** (self.failures - 1), MAX_RETRY_SECONDS)
                self.retry_at = self.clock() + delay
                self.log(f"{self.name}.local stopped being advertised (dns-sd exit {self.process.returncode}); "
                         f"retrying in {delay:.0f} s")
                self._withdraw()
            if address is None or self.clock() < self.retry_at:
                return
        else:
            self._withdraw()
            self.address, self.failures, self.retry_at = address, 0, 0.0
            if address is None:
                self.log(f"No network address; {self.name}.local is paused until this computer rejoins a network.")
                return
            self.log(f"Advertising http://{self.name}.local:{self.port} at {address}.")
        self.process = self.spawn(["dns-sd", "-P", self.name, "_http._tcp", "local", str(self.port),
                                   f"{self.name}.local", address])

    def _withdraw(self) -> None:
        process, self.process = self.process, None
        if process is not None:
            process.stop()
