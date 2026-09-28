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

## 0.2.0 — 2026-09-28
<!-- contract-sha256: 407c91d0c0cb76a9b9b7a89f8123c80f469608fdeee111cd7f7bcf4d5f3d3dfe -->

**BREAKING.** Resolves the known issues recorded with 0.1.0 ([issue #17](https://github.com/NicholasHazen/bardic/issues/17)). (Entry in progress.)

- Every JSON error body now carries a machine-readable `code` next to `detail`. Each operation lists its codes per status in `x-bardic-error-codes`.
- An unexpected server defect is now a JSON 500 with code `internal_error`, not plain text. A dangling reference inside stored data is now a 500, no longer a 404.

## 0.1.2 — 2026-09-28
<!-- contract-sha256: 3be45c18b27d3dd84ab1dc0eef9f2cd9b0f7c1da48b0e3f2def9dbd0fed52778 -->

Describes the pronunciations feature (merged from `main`). All changes are additive.

- New **Pronunciations** operations: `listPronunciations`, `addPronunciation`, `updatePronunciation` and `deletePronunciation` under `/api/books/{book_id}/pronunciations`, with the `PronunciationEntry` request and the `BookPronunciation`, `PronunciationWithUsage`, `PronunciationList` and `PronunciationSaved` responses.
- `Book.pronunciations`, which is absent when a book has none.
- `Performance.pronunciation_count`, for cast performances that pinned entries. Cast plans can add a note when the pinned entries differ from the book's.
- Voice previews: an optional `pronunciation` in the request to audition an unsaved respelling, plus `VoicePreview.spoken_text` and `VoicePreview.pronunciation`.

## 0.1.1 — 2026-09-28
<!-- contract-sha256: 23e40e3b213a5dd56e97155088f9ac415d52217160dbf4d3755396cd037a3b5c -->

- Response schemas no longer carry `default` keywords. The server never applies defaults to responses, and generators such as openapi-typescript read a default as "always present". Whether a response field is always present is stated only by `required`, which is unchanged.
- The compatibility rules in `info.description` now explain request defaults and the matching generator setting (openapi-typescript `defaultNonNullable: false`).
- Verified with a strict TypeScript compile of openapi-typescript 7.13 output (`npm run contract:codegen`).

## 0.1.0 — 2026-09-28
<!-- contract-sha256: 44474856678f54acf0938bfa9e4920a910c503b4f95af786e94710e49202fdb3 -->

First published contract. It describes the existing API without changing server behavior.

- All 96 operations have an operation ID, a summary and description, and a response schema or media type. Each also lists its documented error statuses and a cost class (`x-bardic-cost`).
- Every schema, field and parameter is described. Storage bookkeeping that reaches the wire is marked `x-bardic-internal`.
- Behaviors that clients may want fixed are listed in [docs/API-KNOWN-ISSUES.md](../docs/API-KNOWN-ISSUES.md).
