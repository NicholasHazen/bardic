# Analysis providers

Implemented September 27, 2026. Literary analysis and audio generation have separate provider choices. One selected analysis provider runs the requested progressive phase: discovery, book-level character profiles, or scene/passage direction. Fast discovery and detailed interpretation models are configured separately. Cross-book identities require confirmed links; “global cast” in early design notes means the cast within one book. The resulting cast and directions can be performed by either existing narration provider.

| Analysis choice | Default model | Credential | Transport |
| --- | --- | --- | --- |
| Local draft | Built-in speech-tag heuristics | None | No network |
| Gemini | `gemini-3.8-flash` | `GEMINI_API_KEY` or `GOOGLE_API_KEY` | Gemini `generateContent` |
| OpenAI | `gpt-6-sol` | `OPENAI_API_KEY` | Responses API |
| Anthropic | `claude-sonnet-5` | `ANTHROPIC_API_KEY` | Messages API |

Settings accepts another model ID for each cloud provider, so model access and future versions do not require a code change. A custom model must support its provider's structured-output contract. Credentials, endpoints, and request formats are isolated by provider; a failed or unavailable provider never triggers a fallback to another company. Narration keeps its own Gemini model setting.

## Shared pipeline

1. Discover characters across bounded source chunks with exact source quotations.
2. Reconcile names and aliases into a global cast, preserving reviewed identities and voice assignments.
3. Analyze scene context, assign known speakers, annotate tone/subtext/performance directions, and propose additional scene boundaries using existing source IDs.
4. Validate all results against the original text and known IDs before publishing each chapter stage. Keep manual corrections. Cancellation, refusal, incomplete output, or invalid evidence preserves published chapters and validated request checkpoints; retry resumes compatible saved work. One evidence correction attempt is allowed before stopping. See [chapter analysis](CHAPTER-ANALYSIS.md) for source anchoring and character reference storage.

The pipeline does not ask a model to rewrite source prose. All providers use the same JSON schemas and application validators. Switching analysis provider can change automatic annotations; any takes whose effective directions change are invalidated through the existing audio cache rules.

## API contracts

- OpenAI uses `POST https://api.openai.com/v1/responses`, bearer authorization, `text.format` with a strict JSON schema, and `store: false`. The parser reads text from completed message output items and rejects refusals and incomplete responses. See [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs), [Responses API](https://developers.openai.com/api/reference/resources/responses/methods/create), and the [model catalogue](https://developers.openai.com/api/docs/models).
- Anthropic uses `POST https://api.anthropic.com/v1/messages`, `x-api-key`, `anthropic-version: 2023-06-01`, and `output_config.format` with a JSON schema. The parser requires a completed text response (`end_turn`), and rejects refusals or output-limit termination. See [Structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs), [Messages API](https://platform.claude.com/docs/en/api/messages/create), and the [model catalogue](https://platform.claude.com/docs/en/models/overview).
- Gemini retains its `generateContent` structured JSON adapter. See [structured outputs](https://ai.google.dev/gemini-api/docs/structured-output) and the existing [pipeline research](RESEARCH-PIPELINE.md).

For local runs, `python -m bardic` loads `.env` beside the project's `pyproject.toml` at startup. Existing environment variables take precedence over matching file entries, and values are read without variable interpolation. Restart after editing the file; parent directories are not searched. See [`.env.example`](../.env.example) for provider variables.

Keys entered or cleared in Settings affect only server memory; Settings does not edit `.env`. A restart reloads configured file or environment keys. Keys are never returned in status responses or stored in SQLite/browser storage. Selected provider and per-provider model IDs persist locally. Jobs snapshot their selected configuration and use only that provider's credential. Status shows whether a key is configured; it does not claim the key or chosen model has been authenticated.

Settings provides [account checks](ACCOUNT-CHECKS.md) for the selected analysis models. These report their own token usage and link to billing dashboards, without claiming an exact credit balance. Dated live checks and one real-chapter Anthropic analysis are recorded in [validation](VALIDATION.md); those results do not establish the current state of a credential. Complete-book cloud analysis remains unverified. Automated tests exercise all three provider contracts.
