# Documentation map

Bardic is a local-first ebook analysis, audiobook production and read-along app, originally named Spin Tails. This directory documents the implemented system and the work still needed. Current reference pages include the Bardic rename following the September 27, 2026 repository baseline. Model and pricing research is dated separately.

## Choose a starting point

| I want to… | Start with |
| --- | --- |
| Run the app and use my library | [Project README](../README.md), [operations](OPERATIONS.md) |
| Update an existing Spin Tails installation | [Rename and compatibility details](OPERATIONS.md#upgrading-from-spin-tails) |
| Understand the implementation | [Architecture](ARCHITECTURE.md), [data model](DATA-MODEL.md) |
| Make a change as a human | [Contributing](../CONTRIBUTING.md), [development](DEVELOPMENT.md) |
| Make a change as an agent | [AGENTS.md](../AGENTS.md), then the same architecture/development references |
| Build or restyle browser UI | [UI guide](UI-GUIDE.md), `/static/kitchen-sink.html` |
| Call an endpoint or build a client | [API guide](API.md) for concepts, the [API reference](../contract/API-REFERENCE.md) and the normative [contract/openapi.json](../contract/openapi.json) |
| Change an endpoint | [API workflow](API-WORKFLOW.md): describe it in `bardic/apispec/`, regenerate `contract/`, record it in the [contract changelog](../contract/CHANGELOG.md) |
| Plan the next work | [Roadmap](ROADMAP.md), [decisions](DECISIONS.md) |
| Inspect provenance and reusable outputs | [Data model](DATA-MODEL.md), [artifacts and storage](ARTIFACTS-AND-STORAGE.md) |
| Understand series, library controls and simple listening | [Library, listening and resources](LIBRARY-LISTENING-RESOURCES.md) |
| Diagnose a stopped job or unexpected cost | [Operations](OPERATIONS.md), [progressive analysis](PROGRESSIVE-ANALYSIS-PLAN.md) |
| Assess what has actually been verified | [Validation record](VALIDATION.md) |

## Current implementation references

- [ARCHITECTURE.md](ARCHITECTURE.md): process boundaries, module ownership, request/job flow, browser integration and subsystem relationships.
- [DATA-MODEL.md](DATA-MODEL.md): SQLite/files, source coordinates, evidence, current projections, immutable artifacts, identities and reuse rules.
- [API.md](API.md): cross-cutting API concepts: transport and security, conventions, errors, jobs and cost.
- [API-WORKFLOW.md](API-WORKFLOW.md): how every API change updates the checked-in contract, and what the tests enforce.
- [API-KNOWN-ISSUES.md](API-KNOWN-ISSUES.md): defects and inconsistencies found while writing the contract, each needing a keep-or-fix decision.
- [contract/](../contract/): the normative `openapi.json`, its generated [readable reference](../contract/API-REFERENCE.md) and the [contract changelog](../contract/CHANGELOG.md).
- [DEVELOPMENT.md](DEVELOPMENT.md): reproducible setup, isolated development, test map, extension workflows and frontend contracts.
- [UI-GUIDE.md](UI-GUIDE.md): design tokens, the UI primitives in `ui.js`/`components.css`, when to use each, and the heading, type and consent rules. Copy from `/static/kitchen-sink.html`.
- [OPERATIONS.md](OPERATIONS.md): keys/configuration, backups, recovery, troubleshooting and resource accounting limits.
- [ROADMAP.md](ROADMAP.md): implemented capabilities, partial features, discussed follow-ups, acceptance criteria and undecided choices.
- [DECISIONS.md](DECISIONS.md): the rationale and consequences of the current design.
- [LIBRARY-LISTENING-RESOURCES.md](LIBRARY-LISTENING-RESOURCES.md): library/archive, series execution, narrator mode and usage ledger details.
- [ARTIFACTS-AND-STORAGE.md](ARTIFACTS-AND-STORAGE.md): retained outputs, dependency graphs, exports and retrieval choices.
- [STRUCTURE.md](STRUCTURE.md): EPUB labels/anchors, section kinds and non-destructive structure repair.
- [ANALYSIS-PIPELINE.md](ANALYSIS-PIPELINE.md): the step contract, versions, acceptance/rollback, edit locks, API, how to add/remove steps, and the planned steps.
- [CHAPTER-ANALYSIS.md](CHAPTER-ANALYSIS.md): staged evidence validation, checkpoints and reference persistence.
- [ANALYSIS-PROVIDERS.md](ANALYSIS-PROVIDERS.md): the text analysis adapter contracts.
- [ACCOUNT-CHECKS.md](ACCOUNT-CHECKS.md): explicit provider access checks and why balances remain unknown.
- [VALIDATION.md](VALIDATION.md): dated test/browser/live-provider results and limits. Historical successes do not guarantee current account access.

## Design history and research

- [PLAN.md](PLAN.md) records the original product scope and now points to the current implementation references.
- [CLASSIC-REMOVAL.md](CLASSIC-REMOVAL.md) is the staged plan and exact inventory for removing the legacy phase analysis engine and its data (stage 1 done).
- [SERIES-MEMORY-PLAN.md](SERIES-MEMORY-PLAN.md) proposes carrying accepted evidence between series volumes on the step pipeline (a planned follow-up).
- [PROGRESSIVE-ANALYSIS-PLAN.md](PROGRESSIVE-ANALYSIS-PLAN.md) records the progressive analysis design, original defects, implementation sequence and milestone evidence. Its opening findings describe the pre-update system.
- [RESEARCH-PIPELINE.md](RESEARCH-PIPELINE.md) records source parsing, analysis and alignment research.
- [RESEARCH-VOICE.md](RESEARCH-VOICE.md) records dated narration API, voice design and pricing research. Recheck official sources before relying on a rate or adding a provider capability.
- [CLIENT-SERVER-CONTRACT.md](CLIENT-SERVER-CONTRACT.md) reviews UI/API coupling and proposes a generated interface contract and a gated path to separate client and server repositories. Not implemented.
- [WORD-HIGHLIGHTING.md](WORD-HIGHLIGHTING.md) proposes reusable word timing through local forced alignment, with source-mapping rules, quality limits and a staged implementation plan.

## Maintaining the documentation

Keep one authoritative home for each contract, and link instead of copying large schemas into every guide. The code and executable tests determine current behavior; when docs differ, fix the drift alongside the change. The architecture/data model explain responsibilities and invariants; OpenAPI describes validated endpoint shapes; tests show edge cases; research explains why a choice was considered.

Use relative links and synthetic examples. Personal-library screenshots remain local and ignored by Git. Provider requests, source quotations and generated content can be retained inside a user's data directory; they do not belong in repository fixtures unless intentionally authored for testing.

When a roadmap item is implemented, record its acceptance evidence and update its status. Do not leave a feature both “planned” in the current roadmap and “complete” in the product guide. Preserve dated validation history rather than overwriting old live-account outcomes with current assumptions.
