"""The contract pin script: copy, integrity, staleness and changelog reading.

Uses a synthetic contract in tmp_path, not the real one, so it stays valid as the
real contract changes.
"""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "tools" / "contract_pin.py"
spec = importlib.util.spec_from_file_location("contract_pin", SCRIPT)
pin_tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pin_tool)


def make_source(root: Path, version="0.1.1", extra_entries=()):
    contract = root / "contract"
    contract.mkdir(parents=True)
    document = json.dumps({"openapi": "3.1.0", "info": {"title": "T", "version": version}, "paths": {}}, indent=2) + "\n"
    (contract / "openapi.json").write_text(document, encoding="utf-8")
    (contract / "API-REFERENCE.md").write_text("# Reference\n", encoding="utf-8")
    digest = hashlib.sha256(document.encode("utf-8")).hexdigest()
    entries = "".join(extra_entries)
    (contract / "CHANGELOG.md").write_text(
        f"# Contract changelog\n\n## Versioning rules\n\nx\n\n{entries}"
        f"## {version} — 2026-09-28\n<!-- contract-sha256: {digest} -->\n\nCurrent.\n\n"
        f"## 0.1.0 — 2026-09-27\n<!-- contract-sha256: {'0' * 64} -->\n\nFirst.\n", encoding="utf-8")
    return root


def run(*args):
    return pin_tool.main([str(a) for a in args])


def test_pin_copies_files_and_records_hashes(tmp_path):
    source, dest = make_source(tmp_path / "src"), tmp_path / "dst"
    assert run("pin", "--from", source, "--to", dest) == 0
    pin = json.loads((dest / "contract" / "PIN.json").read_text())
    assert pin["version"] == "0.1.1"
    assert set(pin["files"]) == set(pin_tool.FILES)
    for name in pin_tool.FILES:
        assert (dest / "contract" / name).read_bytes() == (source / "contract" / name).read_bytes()
    assert run("check", "--dest", dest) == 0
    assert run("check", "--dest", dest, "--from", source) == 0


def test_check_detects_hand_edited_pin(tmp_path, capsys):
    source, dest = make_source(tmp_path / "src"), tmp_path / "dst"
    run("pin", "--from", source, "--to", dest)
    (dest / "contract" / "openapi.json").write_text('{"info": {"version": "0.1.1"}}\n')
    assert run("check", "--dest", dest) == 1
    assert "edited by hand or damaged" in capsys.readouterr().err


def test_check_detects_a_stale_pin_and_log_lists_what_changed(tmp_path, capsys):
    source, dest = make_source(tmp_path / "src"), tmp_path / "dst"
    run("pin", "--from", source, "--to", dest)
    newer = make_source(tmp_path / "newer", version="0.1.2", extra_entries=[])
    # The newer source keeps the pinned version's entry in its changelog and adds a heading above it.
    changelog = (newer / "contract" / "CHANGELOG.md").read_text()
    assert "## 0.1.2" in changelog and "## 0.1.0" in changelog
    changelog = changelog.replace("## 0.1.0 — 2026-09-27", "## 0.1.1 — 2026-09-28\n<!-- contract-sha256: " + "1" * 64 + " -->\n\nOld.\n\n## 0.1.0 — 2026-09-27")
    (newer / "contract" / "CHANGELOG.md").write_text(changelog)
    assert run("check", "--dest", dest, "--from", newer) == 1
    assert "stale" in capsys.readouterr().err
    assert run("log", "--dest", dest, "--from", newer) == 0
    out = capsys.readouterr().out
    assert "## 0.1.2" in out and "## 0.1.1" not in out


def test_pin_refuses_a_source_changed_after_its_version_was_recorded(tmp_path, capsys):
    source = make_source(tmp_path / "src")
    (source / "contract" / "openapi.json").write_text(
        json.dumps({"openapi": "3.1.0", "info": {"title": "T", "version": "0.1.1"}, "paths": {"/x": {}}}) + "\n")
    assert run("pin", "--from", source, "--to", tmp_path / "dst") == 1
    assert "changed after 0.1.1 was recorded" in capsys.readouterr().err
    assert not (tmp_path / "dst" / "contract").exists()


def test_pin_refuses_same_directory(tmp_path):
    source = make_source(tmp_path / "src")
    with pytest.raises(SystemExit):
        run("pin", "--from", source, "--to", source)


def test_the_real_contract_is_pinnable(tmp_path):
    """The checked-in contract must always satisfy the pin script's source rules."""
    root = Path(__file__).resolve().parent.parent
    assert pin_tool.source_problems(root) == []
