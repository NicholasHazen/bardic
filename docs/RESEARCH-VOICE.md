# Narration research and provider implementation

Researched **September 27, 2026** against current provider documentation. No paid API request was made during development. The self-hosted Breeze server was checked live with short synthetic requests; see [Breeze TTS](#breeze-tts-self-hosted). The Gemini integration is contract-tested with representative responses; a real project/key still needs a short audition to validate account availability and subjective quality.

## Choice and pipeline

The first implementation uses Gemini 3.8 Flash TTS for directed performance and installed macOS voices for a working offline path. A self-hosted Breeze server was added on 2026-09-27 as a third provider behind the same per-passage recipe. A provider owns one take at a time; the application owns casting, exact source spans, scene context, checkpoints, and assembly. This makes individual takes editable and retryable without regenerating the whole book.

One take per speaker passage avoids the two-speaker limit in Gemini's combined requests. The immutable passage text is sent intact. Character delivery, scene mood/direction, passage direction, and cues are passed separately as style. We deliberately do not insert new words, laughter, or inline tags into the book. Scene segmentation is independent of audio chunking.

Gemini documents audio output without word timing. The reader therefore highlights passages at their measured clip boundaries. Word-level highlighting requires a subsequent alignment stage; interpolated word timestamps would not be measured synchronization.

Source: [Google speech generation guide](https://ai.google.dev/gemini-api/docs/speech-generation).

## Current models and contract

- Primary: `gemini-3.8-flash-tts`; economical alternative: `gemini-3.8-flash-lite-tts`.
- Compatibility option: `gemini-3.1-flash-tts-preview`, now a legacy preview.
- 3.8 Flash's documented Gemini serving limits are 8,192 input tokens and 16,384 output tokens. Correction (September 28, 2026): responses in this project's ledger report 32 output tokens per audio second, not the 25 quoted for pricing, so one request holds about 512 seconds of audio, not 655. A user forum report observes the same 32/s. Simple listening sizes chunks below that cap; see [chunked chapter listening](LIBRARY-LISTENING-RESOURCES.md#chunked-gemini-chapter-listening).
- Rate limits apply per Google project and model; TPM counts input tokens only, and RPD resets at midnight Pacific time. Sources: [rate limits](https://ai.google.dev/gemini-api/docs/rate-limits), [token-rate forum report](https://discuss.ai.google.dev/t/how-many-tokens-are-actually-used-per-second-for-flash-3-8-tts/184714).
- Google positions Flash for expressive acting and long-form consistency. These are provider claims, not quality guarantees from this project.

Source: [Gemini 3.8 Flash TTS model](https://ai.google.dev/gemini-api/docs/models/gemini-3.8-flash-tts), [legacy 3.1 model](https://ai.google.dev/gemini-api/docs/models/gemini-3.1-flash-tts-preview).

`bardic/audio.py` calls `POST https://generativelanguage.googleapis.com/v1beta/interactions` with the key in the `x-goog-api-key` header. Current-model requests contain user-input text items, optional `speech_metadata` annotations with `style`, an audio response format, and a voice in `generation_config.speech_config`. The parser reads the last model-output audio block from `steps[].content[]`.

Current unary output defaults to a complete 24 kHz, mono, 16-bit PCM WAV. Older 3.1 output defaults to raw PCM and must receive a WAV container. The adapter distinguishes the actual container/MIME type; it does not prepend a second header. Legacy calls use a natural-language transcript prompt rather than 3.8's structured speech metadata.

Google also documents the equivalent `generateContent` route with `parts[].speech_metadata`; the application uses Interactions consistently. Source: [GenerateContent compatibility documentation](https://ai.google.dev/gemini-api/docs/generate-content/speech-generation).

## Cast consistency and voice design

The app accepts prebuilt voice names, the project's stored `voice_...` IDs and, since 2026-09-27, Gemini voices designed in the Voices tab for 3.8. `POST /v1beta/voices` with `store: true` and `type: "prompted"` creates a persona from a natural-language description and returns an ID plus a `sample_audio` WAV; `GET /v1beta/voices` lists stored and prebuilt voices (paginated, filterable by type), `GET /v1beta/voices/{id}` returns the sample again, and `DELETE` removes a stored voice. The documentation shows snake_case REST fields (`display_name`, `sample_audio`), while Google REST APIs often return lowerCamelCase (`displayName`, `sampleAudio`, `nextPageToken`, `expireTime`); the client accepts both. Permanent identity traits belong there; short situational style prompts control delivery. Long character biographies in every style prompt can undermine voice consistency, so the adapter uses the editable character delivery field and keeps biography/evidence in the casting UI.

Stored custom voices are limited to 200/project and have a one-year TTL; every create is billed and stored. Bardic records the design description, model, language, gender, server expiry, a hash of the creating key and a local copy of the sample for each version, requires a confirmation for every create, and deletes unchosen candidates. **Gemini voice creation has not been exercised live by this project**; it is covered only by offline tests against a fake API, so the response casing, sample format and billing are unverified. Voice replication (`type: "replicated"`) is not implemented.

Source: [Google voice design](https://ai.google.dev/gemini-api/docs/voice-design).

## Dated price snapshot

Google's published standard prices, **through December 31, 2026**, are:

| Model | Text input / million tokens | Audio output / million tokens | Derived audio-output cost / hour |
|---|---:|---:|---:|
| 3.8 Flash TTS | $0.50 | $9.00 | $0.81 |
| 3.8 Flash-Lite TTS | $0.50 | $6.00 | $0.54 |

Hourly figures use Google's 25 audio tokens/second. All listed rates double January 1, 2027. Ten hours of output alone would currently be about $8.10/$5.40, excluding text analysis, input tokens, retries, alignment, taxes, or future rate changes. Batch pricing is half standard; this initial worker uses ordinary synchronous requests. Paid-tier inputs/outputs are listed as not used to improve Google's products.

Source: [Gemini pricing](https://ai.google.dev/gemini-api/docs/pricing).

## Alignment and alternative providers

**ElevenLabs** is a useful second performance provider. Its dialogue-with-timestamps API returns audio, per-voice segments, and original/normalized character timing arrays. The endpoint's contract permits up to ten unique voice IDs and recommends at most 2,000 total text characters per request. The overview currently says unlimited speakers; use the stricter API reference when implementing. Source: [Dialogue with timestamps](https://elevenlabs.io/docs/api-reference/text-to-dialogue/convert-with-timestamps).

For Gemini output, a later optional word-alignment pass can preserve source offsets while adding measured timestamps:

- [stable-ts](https://github.com/jianfch/stable-ts) aligns supplied text directly against audio and exports word timings. Its repository also documents an Apple Silicon MLX transcription backend, which should not be assumed to support every forced-alignment operation without a separate test.
- [WhisperX](https://github.com/m-bain/whisperX) supplies phoneme-based alignment. Its documented macOS path uses CPU/int8. Diarization is unnecessary because this pipeline already owns the cast assignment.
- [ElevenLabs forced alignment](https://elevenlabs.io/docs/api-reference/forced-alignment/create) accepts audio plus transcript and returns words/characters with times and loss values. This adds another cloud transfer and account.

These aligners are research options, not installed dependencies or completed features.

## Breeze TTS (self-hosted)

Researched and measured **September 27, 2026** against the owner's server (`breeze-tts-2`, API 1.0.0) using its `/guide.md` and `/openapi.json`. Requests used short original synthetic text only. Breeze has no per-request charge.

**Documented by the server** (provider claims, not verified by this project unless noted below):

- `/v1/speech*` accepts up to 10,000 characters per request; background jobs accept up to 2,000,000. `instruction` is at most 1,000 characters and applies with `cfg_scale` 4 by default.
- English and Chinese only. Voices are `cloned` (a 5–15 s reference clip plus transcript, stable timbre) or `designed` (an instruction only, re-imagined per request, so it can drift between segments). The default seed is 42.
- One GPU renders one generation at a time at roughly one second of compute per audio second. Interactive requests (`/v1/speech*`, previews) pre-empt background jobs. More than 16 waiting interactive requests return `503 server_busy` with `Retry-After`; `503 model_loading` lasts about 80 s after a restart.
- Disconnecting from `/v1/speech/stream` stops generation. Timing is per segment (sentence level); there is no word timing. Inline `(laugh)`, `(sigh)`, `(cough)`, `(clears throat)` and Chinese bracket events are performed, not read. The OpenAI-compatible endpoint substitutes the default voice for unknown names; Bardic does not use it.

**Measured in this session** (sample sizes of one or two each; treat as indicative):

- Output from `output_format: "wav"` and streamed `pcm_24000` is mono, 16-bit, 24 kHz: Bardic's stored take format, so no conversion is needed.
- Segment offsets are Python code points into the sent text. A sample containing an emoji with a variation selector measured 63 code points versus 64 UTF-16 units, and every returned offset sliced the exact segment text.
- `auto` segmentation packed up to two sentences per segment. The observed gaps were 500 ms after a paragraph and 120 ms between segments inside a paragraph.
- Plain speech streamed 4.00 s of audio in 4.15–4.17 s (about 1.04× real time), with first audio after 0.17–0.18 s. The pre-send voice check (voice record plus reference clip) took 0.07–0.09 s. A 27.6 s passage took 29.0 s end to end (1.05×).
- Slower samples, causes not isolated: the first 6.5 s take of a run took 14.1 s (2.2×); a 4.2 s take with an instruction took 7.3 s (1.7×); an earlier non-streamed 5.1 s request took 5.6 s while the server reported busy. The server guide says an instruction costs about 10–15 % and the first use of a voice or input size after a restart up to about 1 s.
- Speech rate on these samples was 13.4–15.5 code points per second. Repeating the same text, voice and seed gave identical duration and sentence timing; waveform bit-identity was not checked (the server says it is not guaranteed).
- Voice revisions were identical across two consecutive checks.

**Voice management, measured the same day** (synthetic text; each test voice was deleted afterwards):

- One design preview of 3.2 s audio took 5.3 s. In a browser run, one request produced two previews of 6.6 s and 7.1 s; its wall time was not measured precisely.
- Preview audio (`/v1/voice-previews/{id}/audio`) and reference clips (`/v1/voices/{id}/reference`) are mono 16-bit 24 kHz WAV.
- A clone upload of that preview WAV succeeded, but the stored reference's bytes differed from the upload: the server re-encodes references. Bardic therefore pins the revision from the reference read back after creation and keeps the auditioned clip itself as the version's audition.
- The pinned revision matched the next voice check; a rename (`PATCH` name) left the revision unchanged; `DELETE` removed the voice from the list.

At about real time, a listener at 1.5× or faster overtakes generation unless the chapter is prepared ahead. Offline tests (`tests/test_narration_providers.py`) cover the documented HTTP/SSE contract with a fake server; they do not measure quality or speed.

## Name pronunciation (respelling trial)

Checked **September 28, 2026**. No supported provider accepts phoneme input for this purpose: the [Gemini speech guide](https://ai.google.dev/gemini-api/docs/speech-generation) describes a verbatim transcript with style annotations and inline vocal tags, and says capitalization conveys emphasis; Breeze documents no lexicon; macOS `say` has a phoneme mode that Bardic does not use. A plain respelling in the sent text is the one mechanism all three read.

Trial: five invented names (Aoibhe, Cthaelor, Eilidh, Ngaiovar, Xhosari) in one original carrier sentence, each spelled four ways: original, natural respelling (`Kaylor`), hyphenated lowercase (`kay-lor`) and hyphenated with capitalized stress (`KAY-lor`). One take per clip on macOS (Samantha) and Breeze (the default cloned voice, fixed seed); one listener judged right/close/wrong against the intended pronunciation. Gemini was not rendered.

| Spelling | macOS right/close/wrong | Breeze right/close/wrong |
| --- | --- | --- |
| Original | 0/0/5 | 2/2/1 |
| Natural respelling | 3/2/0 | 4/1/0 |
| Hyphenated lowercase | 2/2/1 | 4/0/1 |
| Hyphenated, capitalized stress | 1/2/2 | 2/0/3 |

Findings, indicative at this sample size:

- Models differ: Breeze pronounced the two Gaelic-spelled names correctly unaided; macOS missed every original spelling. A respelling must be auditioned on the narrating provider.
- A natural-looking respelling was never judged wrong. It is the default recommendation.
- Hyphenated pieces are read as separate words (`ay-lee` failed on both, plausibly as "aye"). Hyphenation helped only where the natural form merged syllables (`ny-oh-var` right on both, `Nyovar` close on both).
- Capitalized syllables were read as letters or abbreviations (`NY-oh-var` wrong on both). macOS otherwise ignores case: four of its five capitalized clips were sample-identical to the lowercase ones and received the same verdicts, a useful consistency check on the ratings.
- Four of five names had one spelling that was right on both providers; second-syllable stress (`Xhosari`) had none on macOS. Per-provider respellings are therefore optional, not the common case.
- Measured audio showed Breeze hyphenated/capitalized forms were usually longer than the natural form (up to 1.3 s) with a 0.6 s gap around `kay-lor`: respelling style changes pacing as well as phonemes.

## Local validation and operating notes

The macOS adapter discovers installed voices with `say -v ?`, writes transcript input into a temporary file, synthesizes AIFF, then converts it with ffmpeg to mono 24 kHz PCM WAV. It validates frame counts, complete data, and nonzero audio before atomically replacing a take. Assembly copies those PCM frames without inserted silence and computes each boundary from integer sample counts.

Validation detects corrupt, truncated, empty, and entirely silent takes. It does **not** prove faithful pronunciation, detect missing/added words, or ensure a satisfying acting performance. Audition and review remain necessary.

Routine tests use mocked provider responses and require no API key. A real macOS narration and assembly smoke test passed on the development host. To repeat it:

```sh
BARDIC_TEST_SYSTEM_AUDIO=1 .venv/bin/python -m pytest tests/test_audio.py::test_real_macos_narration_and_assembly -q
```

Run that optional check outside a tool sandbox that blocks the macOS speech service. In the restricted development sandbox, `say` returned success with a zero-frame file; validation correctly rejected it. Ordinary application execution outside that sandbox produced a valid audible take.
