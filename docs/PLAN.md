# Spin Tails implementation plan

**Historical initial plan.** This records the first-version scope and early contracts. The implementation has since added progressive analysis, immutable artifacts, series scheduling, library management and independent simple listening. Use the current [architecture](ARCHITECTURE.md), [data model](DATA-MODEL.md), [API guide](API.md) and [roadmap](ROADMAP.md) for development. The contract and endpoint summaries below are an incomplete historical snapshot, not the current API specification.

Build a personal, local audiobook studio, with optional cloud inference. Python 3.12 + FastAPI serves a plain JavaScript browser interface. SQLite stores the library and durable job history; audio and original imports live beside it. No account or hosted service is required.

Status of this plan: the initial working version was implemented and locally verified. See [VALIDATION.md](VALIDATION.md) for dated evidence and unverified cloud/device behavior.

## Milestones

1. Research current Gemini narration contracts and alignment options; record sources and tradeoffs.
2. Import EPUB in spine order and UTF-8 text into immutable chapter text. Derive exact, ordered text spans; never ask an LLM to rewrite the book.
3. Produce an editable cast, scene breakdown, speaker assignments and performance directions. Include a conservative local draft and opt-in Gemini, OpenAI, or Anthropic analysis, with evidence and uncertainty. Select analysis independently of narration.
4. Generate short single-speaker takes using Gemini or installed macOS voices. Cache by text + voice + direction + model, checkpoint every take, allow cancellation and retry.
5. Provide a polished library, production studio and reader. Highlight the exact passage currently playing; persist reading progress and expose download/export.
6. Test text integrity, EPUB edge cases, API contracts, cache invalidation and job recovery. Exercise the real local narration and browser workflow.

## First-version boundaries

- DRM-free EPUB and TXT; no DRM removal.
- Passage-level synchronization is exact to audio clip boundaries; no invented word timestamps. Forced word alignment is a later optional stage.
- A voice preset plus a stable performance profile is a repeatable recipe, not a guarantee against timbre variation.
- Cloud requests happen only for explicit analysis/generation actions with a configured key. Local analysis is a reviewable draft, not a complete literary interpretation.
- Complete chapter WAVs plus text, timings and production metadata are exported in a ZIP. M4B/EPUB Media Overlays can build on the same segment timeline later.

## Initial internal contract snapshot

Book JSON:
`{id,title,author,source_name,created_at,chapters,characters,scenes,segments,analysis,revision}`.

Chapter: `{id,index,title,text}`. Offsets are Python Unicode code points and refer to immutable chapter text. The UI uses segment IDs and exact text, not JavaScript slicing by these offsets.

Character: `{id,name,aliases,description,evidence,voice,system_voice,direction}`. Narrator ID is `narrator`; uncertain dialogue is `unassigned`. Both are present in every book. Evidence is a list of exact source quotations.

Scene: `{id,chapter_id,title,summary,tone,direction,segment_ids,character_ids}`.

Segment: `{id,chapter_id,scene_id,start,end,text,kind,speaker_id,confidence,direction,cues,audio}`. `kind` is `narration` or `dialogue`; `audio` is initially null, then `{fingerprint,duration,url,provider,model,voice}`. Whitespace is preserved by spans, including gaps the reader renders separately.

Analysis: `{provider,model?,status,notes}`. Status `partial`, `draft`, or `reviewed`; review is human editing, never automatically asserted by an LLM. Chapter checkpoints and source-linked character references live in dedicated SQLite tables; see [chapter analysis](CHAPTER-ANALYSIS.md).

Job: `{id,book_id,kind,status,progress,total,message,error,created_at,updated_at}`; states `queued,running,completed,failed,cancelled,interrupted`. Workers checkpoint each validated analysis request and audio take. Interrupted work resumes from compatible saved results.

API: `GET /api/status`; `GET/POST /api/books` (POST multipart file); `POST /api/demo`; `GET /api/books/{id}`; `PATCH /api/books/{id}/characters/{id}`; `PATCH /api/books/{id}/segments/{id}`; `PATCH /api/books/{id}/scenes/{id}`; `POST /api/books/{id}/analyze {provider}`; `POST /api/books/{id}/render {provider,scene_id?,segment_id?,force?}`; `GET /api/jobs?book_id=`; `POST /api/jobs/{id}/cancel`; `POST /api/settings {api_key?,tts_model?,analysis_model?}`; `GET /api/audio/{book_id}/{segment_id}`; `GET /api/books/{id}/export`.

Settings also accept `{api_keys?:{gemini?,openai?,anthropic?},analysis_models_by_provider?:{gemini?,openai?,anthropic?},analysis_provider?}`. The original `api_key` and `analysis_model` fields remain Gemini aliases for compatibility. Analysis defaults to the saved provider when no provider is supplied in a request. A queued job captures its provider, model, and credential so subsequent settings changes do not reroute it.

Settings keys stay server-side and are held in memory. Local startup with `python -m spintails` loads the project's `.env` beside `pyproject.toml`, without searching parent folders or interpolating values. Existing environment variables take precedence over matching file entries. `GEMINI_API_KEY` (or `GOOGLE_API_KEY`), `OPENAI_API_KEY`, and `ANTHROPIC_API_KEY` configure provider keys. Settings key changes do not edit `.env`; restarting reloads file/environment keys, so `.env` edits also require a restart. Only model/provider preferences are saved to SQLite. Data directory defaults to `.spintails`, override with `SPINTAILS_DATA_DIR`.
