"""The harness must end the server it started when a session is interrupted or killed politely, and its
raw-socket path (an oversized upload answered before the body is sent) must work."""
from __future__ import annotations

import json
import os
import signal
import socketserver
import subprocess
import sys
import threading
import time

import pytest

from ..client import Api, ContractViolation, Recorder
from .procs import alive, gone
from .tiny import contract as tiny_contract

# Answers the first request (the readiness poll) at once and stalls every later one, so a session started
# against it is stuck in a real request when the signal arrives.
STALLING_SERVER = r'''
import http.server, json, os, pathlib, subprocess, sys, time
port = int(os.environ["BARDIC_PORT"])
grandchild = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"])
pathlib.Path(os.environ["PIDFILE"]).write_text(json.dumps({"server": os.getpid(), "grandchild": grandchild.pid}))
served = []
class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if not served:
            served.append(self.path)
            self.send_response(200); self.send_header("content-type", "application/json"); self.end_headers(); self.wfile.write(b"{}")
            return
        time.sleep(300)
    do_POST = do_GET
    def log_message(self, *args):
        pass
http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
'''


def _run_session_and_signal(tmp_path, sig, repo_root):
    script = tmp_path / 'stalling_server.py'
    script.write_text(STALLING_SERVER)
    pidfile = tmp_path / 'pids.json'
    session = subprocess.Popen(
        [sys.executable, '-m', 'pytest', 'conformance/test_transport.py', '-x', '-q', '-p', 'no:cacheprovider',
         '--server-cmd', f'{sys.executable} {script}', '--server-env', f'PIDFILE={pidfile}', '--server-timeout', '30',
         '--basetemp', str(tmp_path / 'base')],
        cwd=repo_root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True)
    try:
        deadline = time.monotonic() + 30
        while not pidfile.exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        assert pidfile.exists(), 'the session never started its server'
        time.sleep(1.5)  # let the first request stall
        pids = json.loads(pidfile.read_text())
        assert alive(pids['server']) and alive(pids['grandchild'])
        os.kill(session.pid, sig)
        session.wait(timeout=60)
        return pids
    finally:
        if session.poll() is None:
            os.killpg(session.pid, signal.SIGKILL)
            session.wait()


@pytest.mark.parametrize('sig', [signal.SIGINT, signal.SIGTERM], ids=['ctrl-c', 'sigterm'])
def test_an_interrupted_session_leaves_no_server_behind(tmp_path, sig):
    from ..server import REPO_ROOT
    pids = _run_session_and_signal(tmp_path, sig, REPO_ROOT)
    assert gone(pids['server'], 15), 'the server the session started is still running'
    assert gone(pids['grandchild'], 15), 'a process the server started is still running'


# ---------------------------------------------------------------- raw-socket requests

class _EarlyRefusal(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.recv(4096)  # the headers; the body is never read
        body = b'{"detail":"too large","code":"upload_too_large"}'
        self.request.sendall(b'HTTP/1.1 413 Payload Too Large\r\ncontent-type: application/json\r\ncache-control: no-store\r\n'
                             b'connection: close\r\ncontent-length: ' + str(len(body)).encode() + b'\r\n\r\n' + body)


@pytest.fixture()
def refusing_server():
    server = socketserver.ThreadingTCPServer(('127.0.0.1', 0), _EarlyRefusal)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{server.server_address[1]}'
    server.shutdown()
    server.server_close()


def test_a_reply_sent_before_the_declared_body_is_complete_is_read_and_checked(refusing_server):
    api = Api(refusing_server, tiny_contract(), Recorder())
    reply = api.raw_request('POST', '/api/upload', headers={'Content-Type': 'multipart/form-data; boundary=x', 'Content-Length': '99999999'},
                            body_prefix=b'--x\r\n', expect=413)
    assert reply.code == 'upload_too_large' and reply.operation.id == 'uploadThing'
    with pytest.raises(AssertionError, match='expected status'):
        api.raw_request('POST', '/api/upload', headers={'Content-Length': '99999999'}, body_prefix=b'--x\r\n', expect=200)


def test_multipart_requests_are_checked_for_names_and_presence(refusing_server):
    api = Api(refusing_server, tiny_contract(), Recorder())
    with pytest.raises(ContractViolation, match="'file' is a required property"):
        api.call('uploadThing', form={'count': 1})
    with pytest.raises(ContractViolation, match="undeclared field 'other'"):
        api.call('uploadThing', files={'file': ('a.txt', b'x', 'text/plain')}, form={'other': 'y'})
    reply = api.call('uploadThing', files={'file': ('a.txt', b'x', 'text/plain')}, form={'count': 3}, expect=413)
    assert reply.status == 413
