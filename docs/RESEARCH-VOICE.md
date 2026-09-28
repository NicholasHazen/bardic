# Narration research and provider implementation

Researched **September 27, 2026** against current provider documentation. No paid API request was made during development. The Gemini integration is contract-tested with representative responses; a real project/key still needs a short audition to validate account availability and subjective quality.

## Choice and pipeline

The first implementation uses Gemini 3.8 Flash TTS for directed performance and installed macOS voices for a working offline path. A provider owns one take at a time; the application owns casting, exact source spans, scene context, checkpoints, and assembly. This makes individual takes editable and retryable without regenerating the whole book.

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

The app accepts prebuilt voice names and manually supplied `voice_...` IDs for 3.8. Voice design is a useful later addition to the casting screen: `POST /v1beta/voices` creates a prompted persona and returns an ID plus an audition WAV. Permanent identity traits belong there; short situational style prompts control delivery. Long character biographies in every style prompt can undermine voice consistency, so the adapter uses the editable character delivery field and keeps biography/evidence in the casting UI.

Stored custom voices are limited to 200/project and have a one-year TTL. Save the original design prompt, date, ID, and audition before relying on them for a lengthy production. This version does not create or clone voices automatically.

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

## Local validation and operating notes

The macOS adapter discovers installed voices with `say -v ?`, writes transcript input into a temporary file, synthesizes AIFF, then converts it with ffmpeg to mono 24 kHz PCM WAV. It validates frame counts, complete data, and nonzero audio before atomically replacing a take. Assembly copies those PCM frames without inserted silence and computes each boundary from integer sample counts.

Validation detects corrupt, truncated, empty, and entirely silent takes. It does **not** prove faithful pronunciation, detect missing/added words, or ensure a satisfying acting performance. Audition and review remain necessary.

Routine tests use mocked provider responses and require no API key. A real macOS narration and assembly smoke test passed on the development host. To repeat it:

```sh
BARDIC_TEST_SYSTEM_AUDIO=1 .venv/bin/python -m pytest tests/test_audio.py::test_real_macos_narration_and_assembly -q
```

Run that optional check outside a tool sandbox that blocks the macOS speech service. In the restricted development sandbox, `say` returned success with a zero-frame file; validation correctly rejected it. Ordinary application execution outside that sandbox produced a valid audible take.
