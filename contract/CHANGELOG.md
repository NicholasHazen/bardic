# Contract changelog

Changes to the published HTTP contract (`openapi.json`), newest first. The workflow is in [docs/API-WORKFLOW.md](../docs/API-WORKFLOW.md).

## Versioning rules

`info.version` is the version of the contract, not of the server. **Every change to `openapi.json` gets a new version and an entry here, even a documentation-only change.** That way one version always means exactly one document. Under each version heading, the generator records the SHA-256 of the `openapi.json` it describes. Tests fail if the contract changes after its version was recorded.

While the version is 0.x:

- **Additive changes bump the patch** (0.1.0 → 0.1.1): a new operation, a new optional request field or parameter, a new response field, a new documented error status, a new enum value, or improved documentation.
- **Breaking changes bump the minor** (0.1.x → 0.2.0), and the entry is marked **BREAKING** with what clients must change. Breaking means any of:
  - removing or renaming an operation, operation ID, field, parameter or schema;
  - adding a required request field or parameter, or tightening validation;
  - changing a type or the meaning of a value;
  - a response field that becomes optional or nullable;
  - removing an enum value;
  - changing a success status, media type or documented header;
  - removing a documented error status.

From 1.0, which comes with the first dedicated client release, additive changes bump the minor and breaking changes bump the major.

The generator records the version but does not classify the change: the author and the reviewer do. If two branches claim the same version, the changelog conflicts. Resolve it by giving the later change the next version: update `VERSION`, delete that entry's `contract-sha256` line, and regenerate.

## 0.1.0 — 2026-09-28
<!-- contract-sha256: 44474856678f54acf0938bfa9e4920a910c503b4f95af786e94710e49202fdb3 -->

First published contract. It describes the existing API without changing server behavior.

- All 96 operations have an operation ID, a summary and description, and a response schema or media type. Each also lists its documented error statuses and a cost class (`x-bardic-cost`).
- Every schema, field and parameter is described. Storage bookkeeping that reaches the wire is marked `x-bardic-internal`.
- Behaviors that clients may want fixed are listed in [docs/API-KNOWN-ISSUES.md](../docs/API-KNOWN-ISSUES.md).
