"""Startup configuration must be local, literal, and subordinate to the shell."""
import os

import pytest
from fastapi.testclient import TestClient

from spintails import config
from spintails import __main__ as entrypoint
from spintails.app import create_app


@pytest.fixture
def project(tmp_path, monkeypatch):
    for name in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
                 "SPINTAILS_DATA_DIR", "SPINTAILS_PORT", "PYTHON_DOTENV_DISABLED"):
        # Track absent variables too, so values inserted by dotenv are undone.
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name, raising=False)
    root = tmp_path / "project"
    root.mkdir()
    monkeypatch.setattr(config, "PROJECT_ROOT", root)
    return root


def test_module_startup_loads_file_before_server_configuration(project, tmp_path, monkeypatch):
    data_dir = tmp_path / "library"
    (project / ".env").write_text(
        '# Local credentials\nexport GEMINI_API_KEY="gemini test # literal"\n'
        "OPENAI_API_KEY='openai-${HOME}'\nANTHROPIC_API_KEY=anthropic-test\n"
        f'SPINTAILS_PORT=9876\nSPINTAILS_DATA_DIR="{data_dir}"\n', encoding="utf-8")
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
    assert calls == [("spintails.app:app", "127.0.0.1", 9876)]


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
