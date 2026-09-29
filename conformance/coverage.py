"""The end-of-session coverage report: which operations received a 2xx, and which error codes."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .client import Recorder
from .contract import Contract

NEEDS_PROVIDER = Path(__file__).with_name('needs_provider.txt')


def load_needs_provider(path: Path = NEEDS_PROVIDER) -> dict[str, str]:
    """operationId -> reason, from a file of ``operationId  # reason`` lines."""
    found: dict[str, str] = {}
    if not path.exists():
        return found
    for line in path.read_text(encoding='utf-8').splitlines():
        text, _, reason = line.partition('#')
        if text.strip():
            found[text.strip()] = reason.strip()
    return found


@dataclass
class Coverage:
    total: int
    covered: list[str]
    needs_provider: list[str]
    not_yet: list[str]
    stale_allowlist: list[str]
    allowlisted_but_covered: list[str]
    documented_codes: int
    exercised_codes: int
    unexercised_codes: list[str] = field(default_factory=list)
    codes_by_status: dict[int, set[str]] = field(default_factory=dict)

    @property
    def fails_requirement(self) -> bool:
        """What `--require-coverage` fails on: an operation nobody covered or explained, or a stale allowlist."""
        return bool(self.not_yet or self.stale_allowlist)


def build(contract: Contract, recorder: Recorder, needs: dict[str, str]) -> Coverage:
    ids = sorted(contract.operations)
    covered = [op for op in ids if recorder.succeeded.get(op)]
    uncovered = [op for op in ids if op not in covered]
    documented = {(op.id, status, code) for op in contract.operations.values()
                  for status, codes in op.error_codes.items() for code in codes}
    exercised = set(recorder.error_codes)
    codes_by_status: dict[int, set[str]] = {}
    for _, status, code in exercised:
        codes_by_status.setdefault(status, set()).add(code)
    return Coverage(
        total=len(ids), covered=covered,
        needs_provider=[op for op in uncovered if op in needs],
        not_yet=[op for op in uncovered if op not in needs],
        stale_allowlist=sorted(op for op in needs if op not in contract.operations),
        allowlisted_but_covered=[op for op in covered if op in needs],
        documented_codes=len(documented), exercised_codes=len(documented & exercised),
        unexercised_codes=sorted(f'{op} {status} {code}' for op, status, code in documented - exercised),
        codes_by_status=codes_by_status)


def render(contract: Contract, cov: Coverage, recorder: Recorder, *, verbose: bool = False) -> list[str]:
    lines = [f'contract {contract.version} ({contract.source}): {cov.total} operations, {recorder.calls} checked exchanges',
             f'  2xx received:        {len(cov.covered):3d} of {cov.total}',
             f'  needs fake provider: {len(cov.needs_provider):3d}  (no 2xx; listed in conformance/needs_provider.txt)',
             f'  not yet covered:     {len(cov.not_yet):3d}']
    if cov.not_yet:
        lines.append('  not yet covered:')
        lines += [f'    {op}  ({contract.operations[op].method} {contract.operations[op].path})' for op in cov.not_yet]
    if cov.needs_provider and verbose:
        lines.append('  needs fake provider:')
        lines += [f'    {op}' for op in cov.needs_provider]
    if cov.stale_allowlist:
        lines.append('  WARNING: needs_provider.txt names operations the contract does not have: '
                     + ', '.join(cov.stale_allowlist))
    if cov.allowlisted_but_covered:
        lines.append('  note: allowlisted as needing a provider, but received a 2xx (remove from the list): '
                     + ', '.join(cov.allowlisted_but_covered))
    lines.append(f'  documented error codes exercised: {cov.exercised_codes} of {cov.documented_codes} '
                 '(operation, status, code) triples')
    for status in sorted(cov.codes_by_status):
        lines.append(f'    {status}: ' + ', '.join(sorted(cov.codes_by_status[status])))
    if recorder.unrouted:
        lines.append('  router errors (no operation): '
                     + ', '.join(f'{status} {code or "(no code)"} x{count}'
                                 for (status, code), count in sorted(recorder.unrouted.items())))
    return lines
