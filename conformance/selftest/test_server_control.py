"""Target guards and process hygiene: refusing the owner's service, and always ending what the harness starts.

A tiny stand-in server (standard library only) plays the part of the server under test, on a loopback
port the OS chose. Nothing here touches port 8765 or any real Bardic data.
"""
from __future__ import annotations

import json
import os
import sys
import time

import pytest

from .. import server as control
from ..client import Recorder
from ..coverage import build, load_needs_provider, render
from .procs import alive, gone
from .tiny import contract as tiny_contract

STAND_IN = r'''
import http.server, json, os, pathlib, subprocess, sys
port = int(os.environ["BARDIC_PORT"])
data = pathlib.Path(os.environ["BARDIC_DATA_DIR"])
names = ["BARDIC_DATA_DIR", "BARDIC_PORT", "BARDIC_HOST", "BARDIC_LAN_NAME", "BARDIC_ALLOWED_HOSTS", "GEMINI_API_KEY",
         "GOOGLE_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "BREEZE_TTS_URL", "BREEZE_API_KEY", "EXTRA_SETTING"]
(data / "env.json").write_text(json.dumps({"env": {n: os.environ.get(n) for n in names}, "argv": sys.argv[1:], "cwd": os.getcwd()}))
grandchild = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"])
(data / "pids.json").write_text(json.dumps({"server": os.getpid(), "grandchild": grandchild.pid}))
class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.send_header("content-type", "application/json"); self.end_headers(); self.wfile.write(b"{}")
    def log_message(self, *args):
        pass
http.server.HTTPServer(("127.0.0.1", port), Handler).serve_forever()
'''


# ---------------------------------------------------------------- what the harness will drive

@pytest.mark.parametrize('url', ['http://127.0.0.1:8791', 'http://localhost:8791/', 'http://[::1]:9000', 'http://127.1.2.3:80'])
def test_loopback_targets_are_accepted(url):
    assert control.check_target(url, allow_remote=False, allow_owner_port=False) == url.rstrip('/')


@pytest.mark.parametrize('url', ['http://192.168.1.20:8791', 'http://bardic.local:8791', 'http://0.0.0.0:8791',
                                 'https://example.com', 'http://10.0.0.5'])
def test_non_loopback_targets_are_refused_unless_allowed(url):
    with pytest.raises(control.RefusedTarget, match='not a loopback address'):
        control.check_target(url, allow_remote=False, allow_owner_port=False)
    assert control.check_target(url, allow_remote=True, allow_owner_port=False)


@pytest.mark.parametrize('url', ['http://127.0.0.1:8765', 'http://localhost:8765/', 'http://[::1]:8765'])
def test_the_owners_port_is_refused_unless_allowed(url):
    with pytest.raises(control.RefusedTarget, match='8765'):
        control.check_target(url, allow_remote=False, allow_owner_port=False)
    assert control.check_target(url, allow_remote=False, allow_owner_port=True)


def test_the_owners_port_is_refused_on_a_remote_host_too():
    with pytest.raises(control.RefusedTarget, match='8765'):
        control.check_target('http://192.168.1.20:8765', allow_remote=True, allow_owner_port=False)


@pytest.mark.parametrize('url', ['ftp://127.0.0.1:8791', '127.0.0.1:8791', 'http://127.0.0.1:8791/api', 'http://127.0.0.1:1/?x=1', ''])
def test_malformed_targets_are_refused(url):
    with pytest.raises(control.RefusedTarget):
        control.check_target(url, allow_remote=True, allow_owner_port=True)


def test_free_ports_are_never_the_owners_port():
    ports = {control.free_port() for _ in range(50)}
    assert control.OWNER_PORT not in ports and all(1024 <= port < 65536 for port in ports)


def test_the_default_command_runs_this_repositorys_server():
    command = control.default_command()
    assert command[-2:] == ['-m', 'bardic'] and command[0] in (sys.executable, 'uv')


# ---------------------------------------------------------------- spawning

def spawn(tmp_path, *extra, **kwargs):
    return control.spawn([sys.executable, '-c', STAND_IN, *extra], data_dir=tmp_path / 'library', log_path=tmp_path / 'server.log',
                         cwd=tmp_path, timeout=20, **kwargs)


def test_a_spawned_server_gets_a_scratch_library_a_free_port_and_no_keys(tmp_path, monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY', 'parent-secret')
    monkeypatch.setenv('OPENAI_API_KEY', 'parent-secret')
    monkeypatch.setenv('BARDIC_LAN_NAME', 'somewhere')
    monkeypatch.setenv('BARDIC_ALLOWED_HOSTS', 'evil.example')
    server = spawn(tmp_path, '{port}', '{data_dir}', extra_env={'EXTRA_SETTING': 'yes'})
    try:
        seen = json.loads((tmp_path / 'library' / 'env.json').read_text())
        env, port = seen['env'], server.base_url.rsplit(':', 1)[1]
        assert server.base_url == f'http://127.0.0.1:{port}' and int(port) != control.OWNER_PORT
        assert env['BARDIC_PORT'] == port and env['BARDIC_HOST'] == '127.0.0.1'
        assert env['BARDIC_DATA_DIR'] == str(tmp_path / 'library')
        for name in ('GEMINI_API_KEY', 'OPENAI_API_KEY', 'GOOGLE_API_KEY', 'ANTHROPIC_API_KEY', 'BREEZE_TTS_URL', 'BREEZE_API_KEY',
                     'BARDIC_LAN_NAME', 'BARDIC_ALLOWED_HOSTS'):
            assert env[name] == '', f'{name} must be blank for the server under test'
        assert env['EXTRA_SETTING'] == 'yes'
        assert seen['argv'] == [port, str(tmp_path / 'library')], '{port} and {data_dir} are substituted'
        assert os.path.realpath(seen['cwd']) == os.path.realpath(tmp_path)
    finally:
        server.stop()


def test_stopping_a_server_ends_its_whole_process_group_and_nothing_else(tmp_path):
    bystander = __import__('subprocess').Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
    server = spawn(tmp_path)
    pids = json.loads((tmp_path / 'library' / 'pids.json').read_text())
    assert alive(pids['server']) and alive(pids['grandchild']) and bystander.poll() is None
    server.stop()
    assert gone(pids['server']), 'the server process is gone'
    assert gone(pids['grandchild']), 'a process the server started is gone too'
    assert bystander.poll() is None, 'an unrelated process is untouched'
    bystander.kill()
    bystander.wait()
    server.stop()  # stopping twice is harmless


def test_atexit_cleanup_ends_a_server_that_was_never_stopped(tmp_path):
    server = spawn(tmp_path)
    pids = json.loads((tmp_path / 'library' / 'pids.json').read_text())
    control._kill_everything()
    assert gone(pids['server']) and gone(pids['grandchild'])
    assert server.process not in control._LIVE


def test_a_server_that_exits_at_once_is_reported_with_its_log(tmp_path):
    with pytest.raises(RuntimeError) as failure:
        control.spawn([sys.executable, '-c', 'print("boom from the server"); raise SystemExit(3)'], data_dir=tmp_path / 'lib',
                      log_path=tmp_path / 'log', cwd=tmp_path, timeout=20)
    assert 'exited with status 3' in str(failure.value) and 'boom from the server' in str(failure.value)
    assert not control._LIVE, 'nothing is left running'


def test_a_server_that_never_answers_is_killed_after_the_timeout(tmp_path):
    started = time.monotonic()
    with pytest.raises(RuntimeError, match='did not answer GET /api/status'):
        control.spawn([sys.executable, '-c', 'import os, pathlib, time; pathlib.Path(os.environ["BARDIC_DATA_DIR"], "pid").write_text(str(os.getpid())); time.sleep(300)'],
                      data_dir=tmp_path / 'lib', log_path=tmp_path / 'log', cwd=tmp_path, timeout=1.5)
    assert time.monotonic() - started < 15
    assert gone(int((tmp_path / 'lib' / 'pid').read_text())), 'the unresponsive server was terminated'
    assert not control._LIVE


def test_a_command_that_does_not_exist_is_reported(tmp_path):
    with pytest.raises(RuntimeError, match='could not start'):
        control.spawn(['/no/such/binary'], data_dir=tmp_path / 'lib', log_path=tmp_path / 'log', cwd=tmp_path, timeout=5)


# ---------------------------------------------------------------- the coverage report

def test_coverage_separates_covered_needs_provider_and_not_yet(tmp_path):
    contract = tiny_contract()
    recorder = Recorder()
    recorder.succeeded = {'listThings': 2, 'getThing': 1}
    recorder.error_codes = {('getThing', 404, 'thing_not_found'): 1, ('createThing', 400, 'name_taken'): 3}
    cov = build(contract, recorder, {'getThingFile': 'needs audio', 'getThing': 'stale claim', 'noSuchOperation': 'typo'})
    assert cov.covered == ['getThing', 'listThings'] and cov.total == len(contract.operations)
    assert cov.needs_provider == ['getThingFile']
    assert cov.not_yet == ['createThing', 'getLatestThing', 'getThingPicture', 'uploadThing']
    assert cov.stale_allowlist == ['noSuchOperation'] and cov.allowlisted_but_covered == ['getThing']
    assert cov.exercised_codes == 2 and cov.documented_codes > 2
    assert cov.codes_by_status == {404: {'thing_not_found'}, 400: {'name_taken'}}
    assert cov.fails_requirement
    text = '\n'.join(render(contract, cov, recorder))
    assert 'createThing' in text and 'noSuchOperation' in text and 'remove from the list' in text


def test_coverage_requirement_passes_when_everything_is_covered_or_explained():
    contract = tiny_contract()
    recorder = Recorder()
    recorder.succeeded = {op: 1 for op in contract.operations if op != 'getThingFile'}
    cov = build(contract, recorder, {'getThingFile': 'needs audio'})
    assert not cov.not_yet and not cov.fails_requirement


def test_needs_provider_file_format(tmp_path):
    path = tmp_path / 'needs.txt'
    path.write_text('# comment\n\nlistThings   # because\ngetThing\n  # indented comment\n')
    assert load_needs_provider(path) == {'listThings': 'because', 'getThing': ''}
    assert load_needs_provider(tmp_path / 'missing.txt') == {}
