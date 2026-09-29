#!/usr/bin/env python3
"""Pin a copy of the Bardic API contract into another repository, and check it.

Standard library only, so this file can be copied into a client or server
repository and run without the Bardic package.

    contract_pin.py pin   --from SOURCE_REPO --to DEST_REPO
    contract_pin.py check --dest DEST_REPO [--from SOURCE_REPO]
    contract_pin.py log   --dest DEST_REPO --from SOURCE_REPO

`pin` copies contract/openapi.json, contract/API-REFERENCE.md and
contract/CHANGELOG.md into DEST_REPO/contract/ and writes
contract/PIN.json: the contract version, the SHA-256 of each copied file and
the source commit. The checked-in openapi.json stays the source of truth; a
pin is a copy that must never be edited by hand.

`check` fails (exit 1) when the pinned files no longer match PIN.json (edited
or damaged), and, with --from, when the source repository's contract differs
from the pin (stale). It also verifies that the source's own CHANGELOG hash
matches its openapi.json, so a source that was changed without a new version is
refused.

`log` prints the changelog entries the source has added since the pinned
version: what a person or agent needs to read to update.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

FILES = ("openapi.json", "API-REFERENCE.md", "CHANGELOG.md")
PIN_FILE = "PIN.json"
PIN_FORMAT = 1
HEADING = re.compile(r"^## (\d+\.\d+\.\d+) — \d{4}-\d{2}-\d{2}\n(?:<!-- contract-sha256: ([0-9a-f]{64}) -->\n)?", re.M)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def contract_dir(repo: Path) -> Path:
    return repo / "contract"


def contract_version(repo: Path) -> str:
    with (contract_dir(repo) / "openapi.json").open(encoding="utf-8") as handle:
        return json.load(handle)["info"]["version"]


def source_commit(repo: Path) -> str | None:
    try:
        result = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def source_problems(repo: Path) -> list[str]:
    """Reasons the source's contract is not in a publishable state."""
    directory = contract_dir(repo)
    problems = [f"source is missing contract/{name}" for name in FILES if not (directory / name).is_file()]
    if problems:
        return problems
    version = contract_version(repo)
    entries = {m.group(1): m.group(2) for m in HEADING.finditer((directory / "CHANGELOG.md").read_text(encoding="utf-8"))}
    if version not in entries:
        problems.append(f"source CHANGELOG has no entry for version {version}")
    elif entries[version] is None:
        problems.append(f"source CHANGELOG entry {version} records no contract-sha256")
    elif entries[version] != sha256(directory / "openapi.json"):
        problems.append(f"source openapi.json changed after {version} was recorded (regenerate and give it a new version)")
    return problems


def read_pin(dest: Path) -> dict:
    path = contract_dir(dest) / PIN_FILE
    if not path.is_file():
        raise SystemExit(f"contract_pin: {path} does not exist; run pin first")
    pin = json.loads(path.read_text(encoding="utf-8"))
    if pin.get("format") != PIN_FORMAT:
        raise SystemExit(f"contract_pin: unsupported {PIN_FILE} format {pin.get('format')!r}")
    return pin


def pinned_problems(dest: Path) -> list[str]:
    pin = read_pin(dest)
    problems = []
    for name in FILES:
        path = contract_dir(dest) / name
        if not path.is_file():
            problems.append(f"pinned contract/{name} is missing")
        elif sha256(path) != pin["files"].get(name):
            problems.append(f"pinned contract/{name} differs from {PIN_FILE} (edited by hand or damaged)")
    if not problems and contract_version(dest) != pin["version"]:
        problems.append(f"openapi.json says version {contract_version(dest)} but {PIN_FILE} says {pin['version']}")
    return problems


def cmd_pin(args) -> int:
    source, dest = Path(args.source).resolve(), Path(args.dest).resolve()
    if source == dest:
        raise SystemExit("contract_pin: source and destination are the same directory")
    problems = source_problems(source)
    if problems:
        print("\n".join(f"contract_pin: {p}" for p in problems), file=sys.stderr)
        return 1
    target = contract_dir(dest)
    target.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        shutil.copyfile(contract_dir(source) / name, target / name)
    pin = {
        "format": PIN_FORMAT,
        "version": contract_version(source),
        "files": {name: sha256(target / name) for name in FILES},
        "source_commit": source_commit(source),
    }
    (target / PIN_FILE).write_text(json.dumps(pin, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Pinned contract {pin['version']} (openapi.json sha256 {pin['files']['openapi.json'][:12]}) into {target}")
    return 0


def cmd_check(args) -> int:
    dest = Path(args.dest).resolve()
    problems = pinned_problems(dest)
    pin = read_pin(dest)
    if args.source:
        source = Path(args.source).resolve()
        problems += source_problems(source)
        if not problems:
            for name in FILES:
                if sha256(contract_dir(source) / name) != pin["files"][name]:
                    problems.append(f"stale: source contract/{name} differs from the pin "
                                    f"(pinned {pin['version']}, source {contract_version(source)}); run pin, then `log` to read what changed")
    if problems:
        print("\n".join(f"contract_pin: {p}" for p in problems), file=sys.stderr)
        return 1
    print(f"Pinned contract {pin['version']} is intact" + (" and current." if args.source else "."))
    return 0


def cmd_log(args) -> int:
    dest, source = Path(args.dest).resolve(), Path(args.source).resolve()
    pin = read_pin(dest)
    text = (contract_dir(source) / "CHANGELOG.md").read_text(encoding="utf-8")
    headings = list(HEADING.finditer(text))
    versions = [m.group(1) for m in headings]
    if pin["version"] not in versions:
        raise SystemExit(f"contract_pin: pinned version {pin['version']} is not in the source changelog")
    newer = versions[:versions.index(pin["version"])]
    if not newer:
        print(f"No changes since {pin['version']}.")
        return 0
    for index, match in enumerate(headings[:len(newer)]):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        print(text[match.start():end].rstrip() + "\n")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    pin = sub.add_parser("pin", help="copy the source repository's contract into the destination and record a pin")
    pin.add_argument("--from", dest="source", required=True, help="repository holding the authoritative contract/")
    pin.add_argument("--to", dest="dest", required=True, help="repository that receives the pinned copy")
    pin.set_defaults(run=cmd_pin)
    check = sub.add_parser("check", help="verify the pinned copy, and with --from that it is current")
    check.add_argument("--dest", required=True)
    check.add_argument("--from", dest="source")
    check.set_defaults(run=cmd_check)
    log = sub.add_parser("log", help="print changelog entries added to the source since the pinned version")
    log.add_argument("--dest", required=True)
    log.add_argument("--from", dest="source", required=True)
    log.set_defaults(run=cmd_log)
    args = parser.parse_args(argv)
    return args.run(args)


if __name__ == "__main__":
    sys.exit(main())
