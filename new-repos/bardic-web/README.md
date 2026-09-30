# Bardic web client

The client half of Bardic v2: library, listening, reading along, plans and offline, in the browser on phones, tablets and computers. It talks to the Bardic server (`bardic-server`) over the HTTP contract.

**Status: pre-implementation.** The design, product spec and client architecture proposal are written. There is no application code yet.

## Start here

| Read | For |
|---|---|
| [docs/PRODUCT-SPEC.md](docs/PRODUCT-SPEC.md) | What Bardic does, promises, requirements with ids, acceptance tests. |
| [design/README.md](design/README.md) | Every screen as an image, by the board name the spec cites (`[Home]`, `[PlanPremium]`…). Source of the canvas is in `design/canvas/`. |
| [docs/UI-GUIDE.md](docs/UI-GUIDE.md) | Look, tokens, components, status words, rules. |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Proposed client structure, stack, offline and sync design. |
| [docs/ROADMAP.md](docs/ROADMAP.md) | Milestones and exit criteria. |
| [contract/](contract/) | A synced copy of the server's OpenAPI contract and its version. |
| [AGENTS.md](AGENTS.md) | Rules for coding agents and contributors. |

## The contract
The contract is owned by the server repository (`docs/contract/openapi.yaml`). This repository keeps a copy in `contract/openapi.yaml` and its version in `contract/VERSION`; update it with `npm run contract:sync`, then regenerate types with `npm run contract:types`. Never edit the copy by hand.

## Design at a glance
Aurora glass: a dark base with glow colours taken from the cover of the book you are in, frosted glass where content sits above them, one lifted accent. Phones are portrait only; tablets portrait and landscape.
