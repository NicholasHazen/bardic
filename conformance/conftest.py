"""Options, the server under test, and the checked API client.

Run ``pytest conformance`` (see conformance/README.md). The suite talks to a server over HTTP only and
judges it by ``contract/openapi.json`` alone. It never imports the ``bardic`` package.
"""
from __future__ import annotations

import shlex
from pathlib import Path

import pytest

from . import coverage as coverage_report
from . import helpers, synthetic
from . import server as server_control
from .client import Api, Recorder
from .contract import Contract

DEFAULT_CONTRACT = server_control.REPO_ROOT / 'contract' / 'openapi.json'


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup('conformance', 'Bardic HTTP conformance suite')
    group.addoption('--base-url', default=None, metavar='URL',
                    help='Drive an already running server at this loopback URL, for example http://127.0.0.1:8791. '
                         'The suite imports books and changes settings: use a scratch server.')
    group.addoption('--server-cmd', default=None, metavar='CMD',
                    help='Spawn this command as the server on a fresh data directory and a free loopback port '
                         '(environment: BARDIC_DATA_DIR, BARDIC_PORT, BARDIC_HOST=127.0.0.1, provider keys blank; '
                         '{port}, {host} and {data_dir} in CMD are substituted). Default: this repository\'s Python server.')
    group.addoption('--contract', default=str(DEFAULT_CONTRACT), metavar='PATH',
                    help='The openapi.json to judge the server by (default: the repository\'s contract/openapi.json).')
    group.addoption('--allow-remote', action='store_true', default=False,
                    help='Permit a --base-url that is not a loopback address.')
    group.addoption('--allow-owner-port', action='store_true', default=False,
                    help='Permit port 8765, where the owner\'s Bardic service normally listens.')
    group.addoption('--require-coverage', action='store_true', default=False,
                    help='Fail the session if any operation neither received a 2xx nor is listed in needs_provider.txt.')
    group.addoption('--server-timeout', type=float, default=60.0, metavar='SECONDS',
                    help='How long to wait for a spawned server to answer GET /api/status (default 60).')
    group.addoption('--server-cwd', default=None, metavar='DIR',
                    help='Working directory of a spawned server (default: the repository root).')
    group.addoption('--server-env', action='append', default=[], metavar='NAME=VALUE',
                    help='Extra environment for a spawned server. May be repeated.')
    group.addoption('--verbose-coverage', action='store_true', default=False,
                    help='List the operations that need a fake provider in the coverage report.')


def pytest_configure(config: pytest.Config) -> None:
    option = config.option
    if option.base_url and option.server_cmd:
        raise pytest.UsageError('--base-url and --server-cmd are alternatives: pass one, or neither to spawn the Python server')
    if option.base_url:
        try:
            option.base_url = server_control.check_target(
                option.base_url, allow_remote=option.allow_remote, allow_owner_port=option.allow_owner_port)
        except server_control.RefusedTarget as refusal:
            raise pytest.UsageError(str(refusal)) from None
    if not Path(option.contract).is_file():
        raise pytest.UsageError(f'--contract {option.contract}: no such file')
    for entry in option.server_env:
        if '=' not in entry:
            raise pytest.UsageError(f'--server-env expects NAME=VALUE, got {entry!r}')
    config._conformance_recorder = Recorder()
    config._conformance_server = None


@pytest.fixture(scope='session')
def contract(pytestconfig: pytest.Config) -> Contract:
    return Contract.load(pytestconfig.option.contract)


@pytest.fixture(scope='session')
def recorder(pytestconfig: pytest.Config) -> Recorder:
    return pytestconfig._conformance_recorder


@pytest.fixture(scope='session')
def server(pytestconfig: pytest.Config, tmp_path_factory: pytest.TempPathFactory):
    """The server under test: an existing one (``--base-url``) or one this session starts and always stops."""
    option = pytestconfig.option
    if option.base_url:
        yield server_control.Server(option.base_url, spawned=False)
        return
    command = shlex.split(option.server_cmd) if option.server_cmd else server_control.default_command()
    root = tmp_path_factory.mktemp('conformance-server')
    env = dict(entry.split('=', 1) for entry in option.server_env)
    process = server_control.spawn(
        command, data_dir=root / 'library', log_path=root / 'server.log',
        cwd=Path(option.server_cwd) if option.server_cwd else server_control.REPO_ROOT,
        extra_env=env, timeout=option.server_timeout)
    pytestconfig._conformance_server = process
    try:
        yield process
    finally:
        process.stop()


@pytest.fixture(scope='session')
def api(server, contract: Contract, recorder: Recorder):
    client = Api(server.base_url, contract, recorder)
    try:
        yield client
    finally:
        client.close()


# ------------------------------------------------------------------ synthetic books

@pytest.fixture(scope='session')
def txt_spec() -> synthetic.SyntheticTxt:
    return synthetic.txt_book()


@pytest.fixture(scope='session')
def epub_spec() -> synthetic.SyntheticEpub:
    return synthetic.epub_book()


@pytest.fixture(scope='session')
def txt_book(api: Api, txt_spec) -> dict:
    """A TXT book imported once; tests that only read it share it. Do not modify it."""
    return helpers.import_book(api, txt_spec)


@pytest.fixture(scope='session')
def epub_book(api: Api, epub_spec) -> dict:
    """An EPUB book (with a cover) imported once; tests that only read it share it. Do not modify it."""
    return helpers.import_book(api, epub_spec)


@pytest.fixture()
def fresh_book(api: Api) -> dict:
    """A new TXT book for a test that modifies it."""
    return helpers.import_fresh_txt(api)


@pytest.fixture(scope='session')
def local_steps(api: Api) -> list[str]:
    """Free local analysis steps that need no other step's result, in pipeline order.

    Chosen from the server's own pipeline definition (`method: plain`), so the suite never has to know step names.
    """
    steps = api.call('getAnalysisPipeline').json['steps']
    chosen = [step['id'] for step in steps if step['method'] == 'plain' and not step['requires'] and not step['inputs']]
    assert chosen, 'the server defines at least one free local step'
    return chosen


# ------------------------------------------------------------------ reporting

def pytest_terminal_summary(terminalreporter, exitstatus, config: pytest.Config) -> None:
    recorder: Recorder = config._conformance_recorder
    if not recorder.calls:
        return
    contract = Contract.load(config.option.contract)
    cov = coverage_report.build(contract, recorder, coverage_report.load_needs_provider())
    terminalreporter.section('Bardic contract coverage')
    for line in coverage_report.render(contract, cov, recorder, verbose=config.option.verbose_coverage):
        terminalreporter.write_line(line)
    started = config._conformance_server
    if started is not None and terminalreporter.stats.get('failed'):
        terminalreporter.section('Server log (tail)')
        terminalreporter.write_line(f'{started.log_path}')
        terminalreporter.write_line(started.log_tail(30))


def pytest_sessionfinish(session: pytest.Session, exitstatus) -> None:
    config = session.config
    recorder: Recorder = config._conformance_recorder
    if not recorder.calls or not config.option.require_coverage:
        return
    contract = Contract.load(config.option.contract)
    cov = coverage_report.build(contract, recorder, coverage_report.load_needs_provider())
    if cov.fails_requirement and session.exitstatus == 0:
        session.exitstatus = 1
