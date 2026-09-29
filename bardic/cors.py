"""Opt-in cross-origin access (CORS) to the HTTP API.

Off by default. Without BARDIC_CORS_ORIGINS the server sends no CORS header,
answers a preflight `OPTIONS` like any unrouted method, and refuses a
cross-origin browser write (`cross_origin_write`). The setting is either `*`
(every origin) or a comma-separated list of exact origins. It never enables
credentials, and it does not touch the trusted Host check that defends against
DNS rebinding. There is no authentication: an allowed origin reads and changes
the whole library and can start paid work.
"""
import os
import re
from collections.abc import MutableMapping
from dataclasses import dataclass

from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

SETTING = "BARDIC_CORS_ORIGINS"
API_PREFIX = "/api/"
METHODS = "GET, HEAD, POST, PUT, PATCH, DELETE, OPTIONS"
REQUEST_HEADERS = "Content-Type, Range, If-None-Match, If-Match, Authorization"
EXPOSED_HEADERS = ("Bardic-Contract-Version, ETag, Content-Range, Content-Length, Accept-Ranges, "
                   "Content-Disposition, Content-Type")
MAX_AGE = "600"
DEFAULT_PORTS = {"http": 80, "https": 443}
# Scheme, host (a name, an IPv4 address or a bracketed IPv6 address) and an optional port; nothing else.
ORIGIN = re.compile(r"(?P<scheme>[a-z][a-z0-9+.-]*)://(?P<host>[a-z0-9._~-]+|\[[0-9a-f:.]+\])(?::(?P<port>[0-9]{1,5}))?")


@dataclass(frozen=True)
class CorsPolicy:
    """Which request origins may use the API from a browser page."""
    any_origin: bool = False
    origins: frozenset[str] = frozenset()

    def allows(self, origin: str | None) -> bool:
        """Whether the request's `Origin` header value is allowed. A request without one is not."""
        if not origin:
            return False
        return self.any_origin or origin.lower() in self.origins

    def apply(self, headers: MutableMapping, origin: str | None) -> None:
        """Add the headers of a non-preflight `/api/` response. `headers` is a response's headers or a dict."""
        if self.any_origin:
            headers["Access-Control-Allow-Origin"] = "*"
        else:
            # The answer depends on the request's Origin, whether or not it is allowed.
            existing = headers.get("Vary")
            if not existing:
                headers["Vary"] = "Origin"
            elif "origin" not in {token.strip().lower() for token in existing.split(",")} and existing.strip() != "*":
                headers["Vary"] = f"{existing}, Origin"
            if not self.allows(origin):
                return
            headers["Access-Control-Allow-Origin"] = origin.lower()
        headers["Access-Control-Expose-Headers"] = EXPOSED_HEADERS


def parse(value: str | None) -> CorsPolicy | None:
    """The policy for a BARDIC_CORS_ORIGINS value; None when it is unset or empty. Raises ValueError if malformed."""
    value = (value or "").strip()
    if value == "*":
        return CorsPolicy(any_origin=True)
    origins = set()
    for entry in value.split(","):
        entry = entry.strip().lower()
        if entry:
            origins.add(_origin(entry))
    return CorsPolicy(origins=frozenset(origins)) if origins else None


def _origin(entry: str) -> str:
    if entry == "null":
        # The origin of a sandboxed frame or a local file page. Any web page can produce it, so listing it
        # is as open as `*` for writes; it is accepted only because the owner wrote it down.
        return entry
    if "*" in entry:
        raise ValueError(f"{SETTING} is '*' alone or a list of exact origins; a wildcard inside a list is not supported: {entry!r}")
    match = ORIGIN.fullmatch(entry)
    if match is None:
        raise ValueError(f"{SETTING} entries are origins such as 'https://app.example' or 'http://localhost:5173': "
                         f"a scheme, a host and an optional port, without a path, query or spaces: {entry!r}")
    scheme, host, port = match["scheme"], match["host"], match["port"]
    if port is not None and not 0 < int(port) < 65536:
        raise ValueError(f"{SETTING} entry has a port outside 1-65535: {entry!r}")
    if port is not None and int(port) != DEFAULT_PORTS.get(scheme):
        return f"{scheme}://{host}:{int(port)}"
    return f"{scheme}://{host}"  # Browsers serialize an origin without its scheme's default port.


def configured() -> CorsPolicy | None:
    """The policy from the environment. Deliberately BARDIC_ only: there is no older spelling of this setting."""
    return parse(os.environ.get(SETTING))


def warning(policy: CorsPolicy | None) -> str | None:
    """A startup warning for a policy as open as any web page."""
    if policy is None:
        return None
    if policy.any_origin:
        return (f"WARNING: {SETTING}=* lets any web page, in any browser that can reach this server, read and change "
                "the library and start paid work. There is no authentication. Use a list of exact origins unless "
                "that is what you want.")
    if "null" in policy.origins:
        return (f"WARNING: {SETTING} lists 'null', the origin of every sandboxed frame and local file page. Any web "
                "page can create one, so this is as open as '*': any of them can read and change the library and "
                "start paid work. There is no authentication.")
    return None


class CorsMiddleware:
    """Answer preflights and add CORS headers on `/api/`, inside the trusted-host check.

    It sits inside TrustedHostMiddleware so that a request with an untrusted Host is refused before a
    preflight can be answered. The outer HTTP middleware still adds `Cache-Control` and the contract
    version to what this produces. An origin the policy does not allow gets exactly the responses it would
    get with CORS off, including 405 for `OPTIONS`.
    """

    def __init__(self, app: ASGIApp, policy: CorsPolicy):
        self.app, self.policy = app, policy

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith(API_PREFIX):
            await self.app(scope, receive, send)
            return
        origin = Headers(scope=scope).get("origin")
        if scope["method"] == "OPTIONS" and self.policy.allows(origin):
            response = Response(status_code=204)
            self.policy.apply(response.headers, origin)
            response.headers["Access-Control-Allow-Methods"] = METHODS
            response.headers["Access-Control-Allow-Headers"] = REQUEST_HEADERS
            response.headers["Access-Control-Max-Age"] = MAX_AGE
            await response(scope, receive, send)
            return

        async def send_with_cors(message: Message) -> None:
            if message["type"] == "http.response.start":
                self.policy.apply(MutableHeaders(scope=message), origin)
            await send(message)

        await self.app(scope, receive, send_with_cors)
