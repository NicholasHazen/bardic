"""Startup configuration must be local, literal, and subordinate to the shell."""
import os
from pathlib import Path
import runpy

import pytest
from fastapi.testclient import TestClient

from bardic import config
from bardic import __main__ as entrypoint
from bardic.app import create_app


@pytest.fixture
def project(tmp_path, monkeypatch):
    for name in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
                 "BARDIC_DATA_DIR", "BARDIC_PORT", "SPINTAILS_DATA_DIR", "SPINTAILS_PORT", "PYTHON_DOTENV_DISABLED"):
        # Track absent variables too, so values inserted by dotenv are undone.
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name, raising=False)
    root = tmp_path / "project"
    root.mkdir()
    monkeypatch.setattr(config, "PROJECT_ROOT", root)
    return root


@pytest.mark.parametrize("prefix", ["BARDIC", "SPINTAILS"])
def test_module_startup_loads_file_before_server_configuration(project, tmp_path, monkeypatch, prefix):
    data_dir = tmp_path / "library"
    (project / ".env").write_text(
        '# Local credentials\nexport GEMINI_API_KEY="gemini test # literal"\n'
        "OPENAI_API_KEY='openai-${HOME}'\nANTHROPIC_API_KEY=anthropic-test\n"
        f'{prefix}_PORT=9876\n{prefix}_DATA_DIR="{data_dir}"\n', encoding="utf-8")
    # Starting from another directory must still use this project's configuration.
    monkeypatch.chdir(tmp_path)
    calls = []

    def run(app, host, port):
        calls.append((app, host, port))
        with TestClient(create_app()) as client:
            runtime = client.app.state.runtime
            assert runtime.store.root == data_dir
            assert runtime.api_keys == {"gemini": "gemini test # literal", "openai": "openai-${HOME}", "anthropic": "anthropic-test"}
            assert "gemini test # literal" not in client.get("/api/status").text
            assert "openai-${HOME}" not in str(runtime.store.settings())

    monkeypatch.setattr(entrypoint.uvicorn, "run", run)
    entrypoint.main()
    assert calls == [("bardic.app:app", "127.0.0.1", 9876)]


def test_shell_values_win_even_when_intentionally_empty(project, monkeypatch):
    (project / ".env").write_text('GEMINI_API_KEY=file-key\nOPENAI_API_KEY=file-key\nSPINTAILS_PORT=9001\n')
    monkeypatch.setenv("GEMINI_API_KEY", "shell-key")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("SPINTAILS_PORT", "9002")
    calls = []
    monkeypatch.setattr(entrypoint.uvicorn, "run", lambda *args, **kwargs: calls.append(kwargs))
    entrypoint.main()
    assert os.environ["GEMINI_API_KEY"] == "shell-key"
    assert os.environ["OPENAI_API_KEY"] == ""
    assert calls[0]["port"] == 9002


def test_missing_project_file_never_searches_parent_or_working_directory(project, tmp_path, monkeypatch):
    (tmp_path / ".env").write_text('GEMINI_API_KEY=unrelated-parent-key\nSPINTAILS_PORT=9999\n')
    monkeypatch.chdir(tmp_path)
    calls = []
    monkeypatch.setattr(entrypoint.uvicorn, "run", lambda *args, **kwargs: calls.append(kwargs))
    entrypoint.main()
    assert "GEMINI_API_KEY" not in os.environ
    assert calls[0]["port"] == 8765


@pytest.mark.parametrize("shell_prefix", ["BARDIC", "SPINTAILS"])
@pytest.mark.parametrize("file_prefix", ["BARDIC", "SPINTAILS"])
def test_shell_settings_override_both_file_spellings(project, monkeypatch, shell_prefix, file_prefix):
    (project / ".env").write_text(f'{file_prefix}_PORT=9001\n{file_prefix}_DATA_DIR=file-library\n')
    monkeypatch.setenv(f"{shell_prefix}_PORT", "9002")
    monkeypatch.setenv(f"{shell_prefix}_DATA_DIR", "shell-library")
    config.load_project_env()
    assert config.environment_value("PORT") == "9002"
    assert config.data_directory() == Path("shell-library")


@pytest.mark.parametrize("source", ["shell", "file"])
def test_bardic_spelling_wins_within_the_same_configuration_source(project, monkeypatch, source):
    settings = {"SPINTAILS_PORT": "9001", "BARDIC_PORT": "9002",
                "SPINTAILS_DATA_DIR": "old-library", "BARDIC_DATA_DIR": "new-library"}
    if source == "shell":
        for name, value in settings.items():
            monkeypatch.setenv(name, value)
    else:
        (project / ".env").write_text("".join(f"{name}={value}\n" for name, value in settings.items()))
    config.load_project_env()
    assert config.environment_value("PORT") == "9002"
    assert config.data_directory() == Path("new-library")


@pytest.mark.parametrize("directories, expected", [
    ([], ".bardic"),
    ([".spintails"], ".spintails"),
    ([".bardic"], ".bardic"),
    ([".spintails", ".bardic"], ".bardic"),
])
def test_library_selection_is_nonmutating(project, tmp_path, monkeypatch, directories, expected):
    monkeypatch.chdir(tmp_path)
    for directory in directories:
        (tmp_path / directory).mkdir()
    before = set(tmp_path.iterdir())
    assert config.data_directory() == Path(expected)
    assert set(tmp_path.iterdir()) == before


def test_bardic_reopens_legacy_library_in_place(project, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("bardic.app.list_system_voices", lambda: [])
    legacy = tmp_path / ".spintails"
    with TestClient(create_app(legacy)) as client:
        response = client.post('/api/books', files={'file': ('original.txt', b'The moon shone over a silent lake.', 'text/plain')})
        assert response.status_code == 200
        original_book = response.json()
    with TestClient(create_app()) as client:
        assert client.app.state.runtime.store.root == legacy
        book = client.get(f"/api/books/{original_book['id']}").json()
        assert book['chapters'] == original_book['chapters']
        assert client.get('/openapi.json').json()['info']['title'] == 'Bardic'
    assert not (tmp_path / '.bardic').exists()
    assert (legacy / 'originals' / book['id'] / 'source.txt').read_bytes() == b'The moon shone over a silent lake.'


def test_legacy_module_launches_bardic(project, monkeypatch):
    calls = []
    monkeypatch.setattr(entrypoint.uvicorn, "run", lambda *args, **kwargs: calls.append((args, kwargs)))
    runpy.run_module("spintails", run_name="__main__")
    assert calls == [(("bardic.app:app",), {"host": "127.0.0.1", "port": 8765})]
