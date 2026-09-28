# Contributing to Spin Tails

Spin Tails turns personal fiction ebooks into reusable story analysis and narrated read-along experiences. Humans and coding agents share the same source, tests and documentation. The goal is understandable, inspectable work that preserves source text and avoids repeating expensive inference.

## Start here

Read the [product overview](README.md), [architecture](docs/ARCHITECTURE.md), and [development guide](docs/DEVELOPMENT.md). The [documentation map](docs/README.md) separates current reference material from historical research. Agents also read [AGENTS.md](AGENTS.md).

For your first task, use the original sample story or synthetic text. You do not need API keys or someone else's book to run the regression suite. The development guide includes a separate data-directory launch so you can work beside a running personal library.

## What a good change preserves

The source is the authority. Canonical chapter text and its coordinates remain stable while annotations, structure labels, character profiles, directions and voices evolve around it. Models propose annotations; source validation determines whether evidence can be accepted. An uncertain speaker remains uncertain until resolved.

The system saves work at several levels: book projections for the UI, accepted units for inexpensive resume, immutable artifacts and observations for provenance, and request/operation ledgers for resource accounting. Changes should preserve their separate roles. A new cache key must be justified by the inputs the step actually uses. A voice change should not redo character discovery; a profile informed by new earlier-volume evidence must not remain falsely current.

Series context is scoped by confirmed identities and reading order. A missing book is a gap in the collection. It is not evidence about events or characters. The simple-listen mode has its own narrator and audio cache so immediate listening does not overwrite production choices.

## Make a change

1. Check the [roadmap](docs/ROADMAP.md) and existing behavior/tests. Describe the user-visible outcome and the affected invariants.
2. Work on a branch for a cohesive change. Preserve unrelated work, and coordinate file ownership when humans or agents work concurrently.
3. Implement along the existing module boundaries. Add regression coverage for meaningful behavior, failure recovery and source/data integrity. Avoid tests that only repeat an implementation detail.
4. Run focused tests while iterating. Run the broader checks appropriate to the final scope before review.
5. Update current docs for changed behavior, schemas, API or operations. Update roadmap status when an expected feature becomes usable. A research option is not complete merely because an adapter stub exists.
6. Review the diff for accidental data, stale comments, surprising dependencies and misleading claims. Commit source, tests, docs and dependency lock changes together when they form one complete change.

## Review expectations

A review description should state the concrete problem and resulting behavior, include the validation performed, and identify material limitations. Link changed contracts and tests when useful. A small change can have a short description; storage, model transport and scheduling changes need enough evidence to evaluate compatibility and recovery.

For example:

```text
When a series run is waiting on later books, completed earlier books can no
longer be moved out of that series. They remain reserved until the parent
run ends, so later profiles see the planned collection.

Validation: lifecycle tests cover completed-child reservations, cancellation,
and incoming membership. No live provider requests were made.
```

Do not claim word-level synchronization, semantic identity resolution, model quality, price accuracy or full-library coverage unless the change actually establishes it. The UI should expose uncertainty and partial progress where they affect decisions.

## Verification

```sh
uv sync --frozen --group dev
uv run --frozen pytest -q
node --test tests/*_test.js tests/*.test.cjs
for script in spintails/static/*.js; do node --check "$script" || exit 1; done
uv lock --check --offline
```

The Node command includes the model-picker `.test.cjs` suite as well as the standalone UI harnesses. There is no npm installation or frontend compilation. Tests use temporary directories, synthetic prose and mocked transports. Some pytest wrappers require Node; they skip when it is absent. The opt-in Mac narration smoke test and manual browser checks are described in [development](docs/DEVELOPMENT.md).

Use the focused test map in that guide. For UI changes, verify in a real browser as well as the harness: open the changed view, exercise loading/errors and switching books, and verify pending work cannot restart playback or dispatch a stale paid plan. Keep personal validation screenshots outside Git.

## Data, credentials and expenses

The repository excludes `.env`, runtime databases, personal ebooks, audio, exports and local validation screenshots. `.env.example` contains blank credentials. Do not add the real library or keys as fixtures. Use original tiny excerpts and response-shaped mocks. A record of a live validation should state scope and result without embedding private prose, account identifiers or credentials.

Model API calls may cost money and transmit selected text to the configured provider. Normal tests should not need them. A live smoke test should have an explicit provider, model, bounded input and accepted spending scope. Account checks also make small paid-capable requests; they are not a free credit-balance API. The narration ledger currently reports cost without imposing the text-analysis dollar guard on TTS.

If a credential is accidentally committed, removing the current file is insufficient: rotate it and address the retained history before sharing. Do not print the credential in an issue or review. Preserve evidence without making additional copies of the secret.

## Repository and documentation conventions

- Source links in committed Markdown are relative to the document, so they work in any checkout. Avoid developer-specific absolute paths.
- Keep current contracts in the reference docs. Historical plans and research retain dates and link to their current replacements.
- Dates, model IDs, rates and external API capabilities in research are snapshots. Recheck official provider documentation before changing a transport or price table.
- Keep new dependencies in `pyproject.toml` and update `uv.lock` using uv. Explain why the dependency is needed and how it behaves offline and across target platforms.
- Use explicit schema/prompt/adapter versions when output compatibility or reuse changes. Do not silently redefine an existing retained artifact format.
- Source is hosted at [NicholasHazen/bardic](https://github.com/NicholasHazen/bardic). No CI service/requirement or project license is selected. Repository visibility and application deployment are owner choices; source hosting does not imply a public application service.

For a task handoff, leave a concise description of changed behavior, touched areas, tests, remaining work and any process still running. Do not treat a chat transcript as the only record of an important design decision: add it to [decisions](docs/DECISIONS.md) or the relevant reference.
