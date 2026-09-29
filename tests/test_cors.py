"""Opt-in CORS (BARDIC_CORS_ORIGINS): off by default, `*` or a list of exact origins when set.

Whatever the setting, the trusted Host check is unchanged and no credentials header is ever sent. A preflight,
and the router's own 405 for an unrouted method, match no contract operation, so those requests are sent
without the contract check in conftest, as other tests do for router-level responses. Every ordinary /api
response these tests receive is still validated against the contract.
"""
from contextlib import ExitStack

import conftest
import pytest
from fastapi.testclient import TestClient

from bardic import __main__ as entrypoint, config, cors
from bardic.apispec import VERSION
from bardic.app import create_app

ORIGIN = "http://app.example"
OTHER = "http://evil.example"
PREFLIGHT = {"Origin": ORIGIN, "Access-Control-Request-Method": "POST",
             "Access-Control-Request-Headers": "content-type"}
METHODS = "GET, HEAD, POST, PUT, PATCH, DELETE, OPTIONS"
REQUEST_HEADERS = "Content-Type, Range, If-None-Match, If-Match, Authorization"
EXPOSED = ("Bardic-Contract-Version, ETag, Content-Range, Content-Length, Accept-Ranges, "
           "Content-Disposition, Content-Type")
ROUTER_405 = {"detail": "Method Not Allowed", "code": "route_not_found"}


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    for name in ("BARDIC_CORS_ORIGINS", "SPINTAILS_CORS_ORIGINS", "BARDIC_ALLOWED_HOSTS", "BARDIC_LAN_NAME",
                 "BARDIC_HOST", "GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def serve(tmp_path, monkeypatch):
    """Start a server with the given BARDIC_CORS_ORIGINS (None: unset) and return its client."""
    stack = ExitStack()
    started = []

    def start(setting=None, **options):
        if setting is None:
            monkeypatch.delenv("BARDIC_CORS_ORIGINS", raising=False)
        else:
            monkeypatch.setenv("BARDIC_CORS_ORIGINS", setting)
        library = tmp_path / f"library{len(started)}"  # One server per data directory.
        started.append(library)
        return stack.enter_context(TestClient(create_app(library), **options))

    yield start
    stack.close()


def send(client, method, path, **kwargs):
    """A request without the contract check, for what the contract does not describe."""
    return conftest._send(client, client.build_request(method, path, **kwargs))


def books(client):
    return client.get("/api/books").json()


def credentials_never(response):
    assert "access-control-allow-credentials" not in response.headers


def carries_no_cors(response):
    assert not [name for name in response.headers if name.startswith("access-control-")]
    assert "vary" not in response.headers


def every_api_response_keeps_its_headers(response):
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["bardic-contract-version"] == VERSION


# ---------------------------------------------------------------- off by default

@pytest.mark.parametrize("setting", [None, "", "   ", " , ,"], ids=["unset", "empty", "blank", "only-commas"])
def test_off_by_default_changes_nothing(serve, setting):
    client = serve(setting)
    assert not any(m.cls is cors.CorsMiddleware for m in client.app.user_middleware), "no middleware is even added"

    read = client.get("/api/status", headers={"Origin": ORIGIN})
    assert read.status_code == 200
    carries_no_cors(read)
    every_api_response_keeps_its_headers(read)

    for origin in (ORIGIN, "null"):
        write = client.post("/api/demo", headers={"Origin": origin})
        assert (write.status_code, write.json()["code"]) == (403, "cross_origin_write"), origin
        carries_no_cors(write)
        every_api_response_keeps_its_headers(write)
    assert books(client) == [], "the refused writes created nothing"

    for path in ("/api/status", "/api/no-such-route", "/"):
        preflight = send(client, "OPTIONS", path, headers=PREFLIGHT)
        assert (preflight.status_code, preflight.json()) == (405, ROUTER_405), path
        carries_no_cors(preflight)


def test_off_by_default_still_allows_same_origin_and_originless_writes(serve):
    client = serve()
    assert client.post("/api/demo", headers={"Origin": "http://testserver"}).status_code == 200
    assert client.post("/api/demo").status_code == 200


def test_the_old_prefix_does_not_apply_to_a_new_setting(serve, monkeypatch):
    monkeypatch.setenv("SPINTAILS_CORS_ORIGINS", "*")
    client = serve()
    assert client.post("/api/demo", headers={"Origin": ORIGIN}).status_code == 403


# ---------------------------------------------------------------- `*`

def test_star_answers_a_preflight_on_any_api_path(serve):
    client = serve("*")
    for path in ("/api/status", "/api/books/some-book/analysis-pipeline", "/api/no-such-route"):
        response = send(client, "OPTIONS", path, headers=PREFLIGHT)
        assert response.status_code == 204 and response.content == b"", path
        assert "content-length" not in response.headers and "content-type" not in response.headers, path
        assert response.headers["access-control-allow-origin"] == "*"
        assert response.headers["access-control-allow-methods"] == METHODS
        assert response.headers["access-control-allow-headers"] == REQUEST_HEADERS
        assert response.headers["access-control-max-age"] == "600"
        credentials_never(response)
        every_api_response_keeps_its_headers(response)
    # Only /api/ is covered: the UI and static files keep the router's answer.
    for path in ("/", "/static/app.js"):
        assert send(client, "OPTIONS", path, headers=PREFLIGHT).status_code == 405, path


def test_star_needs_no_particular_preflight_header_but_an_origin(serve):
    client = serve("*")
    assert send(client, "OPTIONS", "/api/status", headers={"Origin": ORIGIN}).status_code == 204
    assert send(client, "OPTIONS", "/api/status").status_code == 405  # Not a browser preflight.
    assert send(client, "OPTIONS", "/api/status", headers={"Origin": ""}).status_code == 405


def test_star_marks_every_api_response_readable_and_exposes_the_headers(serve):
    client = serve("*")
    responses = [client.get("/api/status", headers={"Origin": ORIGIN}),
                 client.get("/api/status"),
                 client.get("/api/books/no-such-book", headers={"Origin": ORIGIN}),  # 404
                 client.post("/api/series", json={}, headers={"Origin": ORIGIN}),  # 422
                 send(client, "GET", "/api/no-such-route", headers={"Origin": ORIGIN})]
    assert [r.status_code for r in responses] == [200, 200, 404, 422, 404]
    for response in responses:
        assert response.headers["access-control-allow-origin"] == "*"
        assert response.headers["access-control-expose-headers"] == EXPOSED
        assert "vary" not in response.headers  # The answer does not depend on the Origin.
        credentials_never(response)
        every_api_response_keeps_its_headers(response)
    # The UI is not the API.
    carries_no_cors(client.get("/"))


def test_star_lets_a_cross_origin_browser_write_through(serve):
    client = serve("*")
    for headers in ({"Origin": ORIGIN}, {"Origin": ORIGIN, "Sec-Fetch-Site": "cross-site"},
                    {"Origin": "https://other.example:8443", "Sec-Fetch-Site": "same-site"}):
        response = client.post("/api/demo", headers=headers)
        assert response.status_code == 200, headers
        assert response.headers["access-control-allow-origin"] == "*"
        credentials_never(response)
    assert len(books(client)) == 3


def test_star_writes_from_the_opaque_origin_null_are_allowed(serve):
    # `*` already means every web page, and any page can make a sandboxed frame whose Origin is "null",
    # so refusing it would protect nothing and break a client opened from a local file.
    client = serve("*")
    assert client.post("/api/demo", headers={"Origin": "null"}).status_code == 200
    assert send(client, "OPTIONS", "/api/demo", headers={"Origin": "null"}).status_code == 204


def test_star_still_refuses_a_cross_site_write_that_names_no_origin(serve):
    client = serve("*")
    response = client.post("/api/demo", headers={"Sec-Fetch-Site": "cross-site"})
    assert (response.status_code, response.json()["code"]) == (403, "cross_origin_write")
    assert books(client) == []


def test_star_keeps_the_guard_outside_the_api(serve):
    client = serve("*")
    assert send(client, "POST", "/", headers={"Origin": ORIGIN}).status_code == 403
    assert send(client, "POST", "/", headers={"Origin": "http://testserver"}).status_code == 405


@pytest.mark.parametrize("setting", ["*", ORIGIN])
def test_the_trusted_host_check_still_runs_first(serve, setting):
    client = serve(setting)
    for host in ("evil.example", "evil.example:8765", "127.0.0.1.evil.example"):
        headers = {"Host": host, "Origin": ORIGIN}
        for method, path in (("GET", "/api/status"), ("OPTIONS", "/api/status"), ("POST", "/api/demo"),
                             ("DELETE", "/api/books/some-book")):
            response = send(client, method, path, headers=headers)
            assert (response.status_code, response.text) == (400, "Invalid host header"), (method, path, host)
            assert "access-control-allow-origin" not in response.headers
            credentials_never(response)
            every_api_response_keeps_its_headers(response)
    assert books(client) == []


def test_a_preflight_is_answered_for_an_added_trusted_host_and_only_for_it(tmp_path, monkeypatch):
    monkeypatch.setenv("BARDIC_CORS_ORIGINS", "*")
    monkeypatch.setenv("BARDIC_ALLOWED_HOSTS", "bardic.example")
    with TestClient(create_app(tmp_path), base_url="http://bardic.example") as client:
        assert send(client, "OPTIONS", "/api/status", headers=PREFLIGHT).status_code == 204
        assert client.get("/api/status", headers={"Host": "other.example"}).status_code == 400


def test_a_defect_that_escapes_the_handlers_is_still_readable_by_the_browser(serve, monkeypatch):
    def defect():
        raise RuntimeError("defect")

    monkeypatch.setattr("bardic.app.make_demo_book", defect)
    client = serve("*", raise_server_exceptions=False)
    response = send(client, "POST", "/api/demo", headers={"Origin": ORIGIN})
    assert (response.status_code, response.json()["code"]) == (500, "internal_error")
    assert response.headers["access-control-allow-origin"] == "*"
    assert response.headers["access-control-expose-headers"] == EXPOSED
    credentials_never(response)
    every_api_response_keeps_its_headers(response)


def test_a_defect_response_names_the_listed_origin_it_answers(serve, monkeypatch):
    def defect():
        raise RuntimeError("defect")

    monkeypatch.setattr("bardic.app.make_demo_book", defect)
    client = serve(ORIGIN, raise_server_exceptions=False)
    allowed = send(client, "POST", "/api/demo", headers={"Origin": ORIGIN})
    assert allowed.status_code == 500
    assert allowed.headers["access-control-allow-origin"] == ORIGIN and allowed.headers["vary"] == "Origin"
    other = send(client, "POST", "/api/demo", headers={"Origin": OTHER})
    assert other.status_code == 403  # Refused by the write guard before the defect is reached.


# ---------------------------------------------------------------- a list of exact origins

LISTED = "http://app.example, HTTP://Localhost:5173 ,https://secure.example:443,http://[::1]:3000"


def test_a_listed_origin_is_echoed_with_vary_and_may_write(serve):
    client = serve(LISTED)
    for origin in ("http://app.example", "http://localhost:5173", "https://secure.example", "http://[::1]:3000"):
        read = client.get("/api/status", headers={"Origin": origin})
        assert read.headers["access-control-allow-origin"] == origin
        assert read.headers["vary"] == "Origin"
        assert read.headers["access-control-expose-headers"] == EXPOSED
        credentials_never(read)
        every_api_response_keeps_its_headers(read)

        preflight = send(client, "OPTIONS", "/api/demo", headers={**PREFLIGHT, "Origin": origin})
        assert preflight.status_code == 204 and preflight.content == b""
        assert preflight.headers["access-control-allow-origin"] == origin
        assert preflight.headers["vary"] == "Origin"
        assert preflight.headers["access-control-allow-methods"] == METHODS
        assert preflight.headers["access-control-allow-headers"] == REQUEST_HEADERS
        assert preflight.headers["access-control-max-age"] == "600"
        credentials_never(preflight)
        every_api_response_keeps_its_headers(preflight)

        write = client.post("/api/demo", headers={"Origin": origin, "Sec-Fetch-Site": "cross-site"})
        assert write.status_code == 200
        assert write.headers["access-control-allow-origin"] == origin
        assert write.headers["vary"] == "Origin"
    assert len(books(client)) == 4


@pytest.mark.parametrize("origin", [
    OTHER,
    "https://app.example",  # another scheme
    "http://app.example:8080",  # another port
    "http://localhost",  # the listed one has a port
    "http://localhost:5174",
    "http://sub.app.example",
    "http://app.example.evil.example",
    "http://evilapp.example",
    "http://[::1]:3001",
    "https://secure.example:444",
    "null",
], ids=repr)
def test_another_origin_gets_no_access_and_the_answers_it_gets_with_cors_off(serve, origin):
    client = serve(LISTED)
    headers = {"Origin": origin}
    read = client.get("/api/status", headers=headers)
    assert read.status_code == 200  # A read is not refused; the browser is not told it may read it.
    assert "access-control-allow-origin" not in read.headers and "access-control-expose-headers" not in read.headers
    assert read.headers["vary"] == "Origin"  # A shared cache must not replay it to a listed origin.
    credentials_never(read)

    write = client.post("/api/demo", headers=headers)
    assert (write.status_code, write.json()["code"]) == (403, "cross_origin_write")
    assert "access-control-allow-origin" not in write.headers
    every_api_response_keeps_its_headers(write)

    preflight = send(client, "OPTIONS", "/api/demo", headers={**PREFLIGHT, "Origin": origin})
    assert (preflight.status_code, preflight.json()) == (405, ROUTER_405)
    assert not [name for name in preflight.headers if name.startswith("access-control-")]
    assert books(client) == []


def test_a_list_setting_still_allows_same_origin_and_originless_writes(serve):
    client = serve(LISTED)
    assert client.post("/api/demo", headers={"Origin": "http://testserver"}).status_code == 200
    assert client.post("/api/demo").status_code == 200
    assert client.post("/api/demo", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403


def test_null_is_refused_from_a_list_unless_it_is_listed(serve):
    assert serve(ORIGIN).post("/api/demo", headers={"Origin": "null"}).status_code == 403
    client = serve(f"{ORIGIN},null")
    write = client.post("/api/demo", headers={"Origin": "null"})
    assert write.status_code == 200 and write.headers["access-control-allow-origin"] == "null"
    assert send(client, "OPTIONS", "/api/demo", headers={"Origin": "null"}).status_code == 204
    assert client.post("/api/demo", headers={"Origin": OTHER}).status_code == 403


def test_a_list_setting_keeps_the_trusted_host_check_and_the_paths_outside_the_api(serve):
    client = serve(ORIGIN)
    assert send(client, "POST", "/", headers={"Origin": ORIGIN}).status_code == 403
    for path in ("/", "/static/app.js"):
        assert send(client, "OPTIONS", path, headers=PREFLIGHT).status_code == 405


# ---------------------------------------------------------------- parsing

@pytest.mark.parametrize("value, origins", [
    ("http://a.example", {"http://a.example"}),
    (" HTTP://A.Example:8080 , https://b.example ,, ", {"http://a.example:8080", "https://b.example"}),
    ("http://a.example:80,https://a.example:443,http://a.example:0080", {"http://a.example", "https://a.example"}),
    ("http://localhost:5173,http://127.0.0.1:5173,http://[::1]:5173",
     {"http://localhost:5173", "http://127.0.0.1:5173", "http://[::1]:5173"}),
    ("tauri://localhost,capacitor://localhost", {"tauri://localhost", "capacitor://localhost"}),
    ("http://a.example,http://a.example,HTTP://A.EXAMPLE", {"http://a.example"}),
    ("null", {"null"}),
    ("NULL, http://a.example", {"null", "http://a.example"}),
])
def test_origin_lists_are_canonical(value, origins):
    policy = cors.parse(value)
    assert policy.origins == origins and not policy.any_origin


def test_star_and_unset_values():
    assert cors.parse(" * ").any_origin
    for value in (None, "", " ", ",", " , , "):
        assert cors.parse(value) is None


MALFORMED = [
    "*.example", "https://*.example", "*,http://a.example", "http://a.example,*", "**", "* *",
    "http://a.example/", "http://a.example/app", "http://a.example?x=1", "http://a.example#top",
    "http://user@a.example", "a.example", "a.example:8080", "//a.example", "http:a.example", "http://", "://a.example",
    "http://a.example:", "http://a.example:0", "http://a.example:65536", "http://a.example:http",
    "http://a b.example", "file://", "file:///tmp/app.html", "http://exämple.test", "https://a.example:8080/",
]


@pytest.mark.parametrize("value", MALFORMED)
def test_a_malformed_setting_is_rejected(value):
    with pytest.raises(ValueError, match="BARDIC_CORS_ORIGINS"):
        cors.parse(value)


def test_the_app_and_the_launcher_refuse_a_malformed_setting(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("BARDIC_CORS_ORIGINS", "https://a.example/app")
    with pytest.raises(ValueError, match="without a path"):
        create_app(tmp_path)
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)  # No .env to load.
    monkeypatch.setattr(entrypoint.lan, "port_in_use", lambda port: False)
    monkeypatch.setattr(entrypoint.uvicorn, "run", lambda *args, **kwargs: pytest.fail("server started"))
    with pytest.raises(SystemExit, match="Bardic: BARDIC_CORS_ORIGINS entries are origins"):
        entrypoint.main()
    monkeypatch.setenv("BARDIC_CORS_ORIGINS", "*,http://a.example")
    with pytest.raises(SystemExit, match="wildcard inside a list"):
        entrypoint.main()


# ---------------------------------------------------------------- startup warning

@pytest.fixture
def launched(tmp_path, monkeypatch, capsys):
    """Run main() with a setting; return what it printed and whether it started the server."""
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)  # No .env to load.
    monkeypatch.setattr(entrypoint.lan, "port_in_use", lambda port: False)
    served = []
    monkeypatch.setattr(entrypoint.uvicorn, "run", lambda *args, **kwargs: served.append((args, kwargs)))

    def launch(setting=None):
        if setting is None:
            monkeypatch.delenv("BARDIC_CORS_ORIGINS", raising=False)
        else:
            monkeypatch.setenv("BARDIC_CORS_ORIGINS", setting)
        entrypoint.main()
        return capsys.readouterr().err, served

    return launch


def test_star_warns_once_at_startup(launched):
    err, served = launched("*")
    assert len(served) == 1
    assert err.count("WARNING") == 1
    assert "BARDIC_CORS_ORIGINS=*" in err and "any web page" in err and "paid work" in err
    assert "no authentication" in err.lower()


def test_null_in_a_list_warns_and_other_lists_and_off_do_not(launched):
    err, _ = launched("http://a.example,null")
    assert err.count("WARNING") == 1 and "'null'" in err and "as open as '*'" in err
    for setting in ("http://a.example,http://localhost:5173", "", None):
        assert launched(setting)[0] == ""


# ---------------------------------------------------------------- headers on a response that has some

def test_vary_is_merged_not_replaced():
    policy = cors.parse(ORIGIN)
    for existing, expected in ((None, "Origin"), ("Accept-Encoding", "Accept-Encoding, Origin"),
                               ("accept-encoding, ORIGIN", "accept-encoding, ORIGIN"), ("*", "*")):
        headers = {} if existing is None else {"Vary": existing}
        policy.apply(headers, ORIGIN)
        assert headers["Vary"] == expected
        assert headers["Access-Control-Allow-Origin"] == ORIGIN
