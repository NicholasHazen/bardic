# Word highlighting: research and proposed implementation

Researched **2026-09-27** against the primary sources linked below. This is a proposal, not an implemented feature or a performance benchmark. No alignment dependency, model download, paid request, or library migration was performed for this research. Bardic currently highlights the playing passage.

## Recommendation

Add an optional local **forced-alignment** step after a WAV has been generated: compare that particular recording with its exact source passage, estimate where each spoken word begins and ends, and retain the result. This can improve existing recordings without narrating them again. Start by benchmarking WhisperX's alignment-only API on short clips in an isolated worker environment; keep passage highlighting when alignment is absent or unreliable.

Do not divide clip duration evenly among words. Pauses, emphasis, laughter, omitted words and pronunciation changes make that look precise while being wrong. Forced alignment also produces estimates, not guaranteed ground truth. It must preserve uncertainty and cannot by itself certify that every supplied word was spoken.

The browser work is relatively small. Reliable alignment, source mapping, quality checks, reusable storage and packaging a local speech model are the larger parts.

## What the current providers expose

**Gemini TTS:** the current speech-generation guide describes audio-only output, complete WAV for unary requests, and PCM audio chunks for streaming. It does not document word timestamps in TTS responses. Treat provider-native TTS alignment as unavailable until a documented contract and a bounded test establish it. Streaming reduces time to initial audio; it does not supply source-word boundaries. [Google TTS guide](https://ai.google.dev/gemini-api/docs/speech-generation)

The Interactions API separately documents `transcription_config`, word timestamp granularity, and `word_info` for **speech recognition**. These are not evidence that a TTS call returns timings. A second transcription request would be another paid processing step and would still need a validated mapping from recognized words to the original ebook. Its response text offsets are documented as bytes; they must never be copied into Bardic's Unicode code-point coordinates. [Google Interactions reference](https://ai.google.dev/api/interactions-api)

**macOS device narration:** Bardic's current `say`/ffmpeg adapter retains WAV audio and duration, with no word-boundary sidecar. The same post-generation alignment step can handle these WAVs. Replacing `say` with a native speech API that exposes callbacks would be a separate adapter project; no callback-to-file timing guarantee has been established here.

## Local candidates and practical limits

| Candidate | Documented capability | Implication for Bardic |
| --- | --- | --- |
| WhisperX alignment | Language-specific acoustic alignment, word timing, and a documented CPU mode for Mac. Maintainers confirm that supplied transcript segments can be passed directly to `whisperx.align`. | First benchmark candidate: use the known passage and its real clip duration; skip transcription and speaker diarization for the alignment path. CPU is the baseline, not an Apple GPU speed promise. |
| Montreal Forced Aligner | Acoustic models, pronunciation dictionaries, single-file/corpus alignment and TextGrid output; newer model bundles can include G2P for unknown words. | Useful comparison if pronunciation control improves fictional names. Its Conda/Kaldi environment and model preparation add packaging work. Native Apple Silicon installation and throughput need an actual clean-environment test. |
| stable-ts | Known-text alignment and an MLX Whisper transcription integration are documented. The repository was archived on May 30, 2026, with development paused. | A comparison tool, not the preferred new default dependency. MLX transcription support is not proof that its known-text alignment path is MLX accelerated. |
| A small custom CTC aligner | Can match a supplied transcript to acoustic-model emissions. | Potentially narrower than a complete transcription stack, but shifts maintenance, normalization and quality work into Bardic. Do not copy the old TorchAudio `forced_align` tutorial as a current dependency recipe: those APIs were removed in 2.9. |

Primary references: [WhisperX project](https://github.com/m-bain/whisperX), [maintainer guidance on known transcripts](https://github.com/m-bain/whisperX/issues/939), [MFA alignment](https://montreal-forced-aligner.readthedocs.io/en/latest/user_guide/workflows/alignment.html), [MFA installation](https://montreal-forced-aligner.readthedocs.io/en/latest/installation.html), [stable-ts project](https://github.com/jianfch/stable-ts), [TorchAudio migration warning](https://docs.pytorch.org/audio/stable/tutorials/ctc_forced_alignment_api_tutorial.html).

WhisperX's documented limitations include words outside the alignment dictionary, illustrated by digits/currency, and overlapping speech. Fiction additionally needs explicit evaluation of invented names, accents, whispered lines, long pauses and nonverbal sounds. Numeric expansion or punctuation normalization must happen in a derived alignment transcript with reversible source mappings, never by changing canonical prose. [WhisperX limitations](https://github.com/m-bain/whisperX#limitations-)

The full WhisperX distribution brings a substantial speech/ML stack; its package manifest includes dependencies beyond alignment. Keep a pinned optional worker environment and downloaded models outside Bardic's small base dependency set. Benchmark startup separately from warm per-clip processing. Do not install the entire stack merely to support timing fields in the application. [WhisperX dependency manifest](https://github.com/m-bain/whisperX/blob/main/pyproject.toml)

## Proposed retained contract

Use the existing [source and artifact rules](DATA-MODEL.md). A new versioned alignment artifact should identify:

- The actual `audio_asset_id`/SHA-256, WAV sample rate and sample count. A rendering recipe alone is insufficient: two performances of the same text have different pauses and timings.
- Source chapter artifact/hash, passage ID and exact chapter-local start/end coordinates, with a hash of the exact passage text.
- Aligner package/version, acoustic model revision, language, normalization/tokenization version, relevant options and any audio preprocessing recipe.
- Clip-relative intervals plus the original source slices they describe: `start_sample`, `end_sample`, `source_start`, `source_end`, and exact `text`.
- Overall state (`ready`, `partial`, `failed`), per-span status/reason, measured coverage and optional engine scores. Missing confidence stays unknown; engine scores are not automatically calibrated probabilities.
- Dependencies on actual retained source and audio inputs, and resource measurements for the attempt. Failed attempts remain distinct from accepted timing artifacts.

An alignment reuse key should hash the **audio content hash + exact text hash + alignment recipe**. Keep source bindings separate: identical audio/text may reuse local timing work at another valid source location without borrowing that location's IDs. Reusing one recording at another playback speed requires no realignment. Generating a new take does. Keep old alignment versions when the model or normalization policy changes.

Source offsets remain zero-based Python Unicode code points with exclusive ends. Validate that each reported text slice exactly matches the immutable chapter. Browser rendering should use server-supplied original text pieces, or an explicit code-point-to-UTF-16 map; JavaScript string indices are not interchangeable with stored offsets. An expanded spoken number may map several acoustic tokens to one original source span. Unsupported or ambiguous mappings should remain unaligned.

Validate finite, nonnegative, ordered intervals within the real audio duration; source ordering and coverage; zero-duration/excessively stretched words; and supplied-versus-recognized discrepancies where a separate verification pass exists. Mark suspect regions for passage fallback. A successful forced alignment can place words that were never spoken, so retain independent speech verification as a separate quality capability.

SQLite plus retained JSON artifacts is sufficient. Neither a vector database nor a new hosted service is needed. Add portable timing records to analysis/audio exports after the format and compatibility behavior are defined.

## Playback at 2–2.5×

Timings belong to the original media clock. Compare the active word interval with `audio.currentTime`; **do not multiply or divide that value by playback speed**. The browser already advances its media position at the chosen rate. Re-evaluate after seek, pause, speed changes and clip changes. The HTML standard defines this media timeline and playback-rate behavior. [HTML media standard](https://html.spec.whatwg.org/multipage/media.html#dom-media-currenttime)

Use a lightweight animation-frame loop while the reader is visible and playing, changing only the active word class. `timeupdate` can be too sparse for fast word highlighting; retain it for progress persistence and as a fallback. Recompute from the media clock when returning from a hidden tab rather than replaying queued highlight events. Animation callbacks can pause in background tabs. [Mozilla's timeupdate documentation](https://developer.mozilla.org/en-US/docs/Web/API/HTMLMediaElement/timeupdate_event), [animation-frame documentation](https://developer.mozilla.org/en-US/docs/Web/API/Window/requestAnimationFrame)

Alignment should be a lower-priority follow-on worker behind imminent audio preparation. Do not stall usable audio because its timing artifact is pending. The reader can start with passage highlighting and use word timing once ready. This protects simple listening if an aligner fails or cannot keep up.

The queue should measure **playable seconds**, not only passage count. At 2.5×, 75 seconds of original audio provides 30 seconds of listening. For sequential preparation, sustained generation-plus-alignment time must average less than 0.4 seconds per original audio second to replenish as fast as consumption; overlapping workers have different bottlenecks. Buffering absorbs temporary latency, not a permanently slower producer. These are scheduling relationships, not measured provider performance.

## Delivery phases and acceptance evidence

1. **Bounded local benchmark.** Use original synthetic prose and representative short recordings, including names, digits, Unicode, silence, repeated/omitted words and expressive cues. Compare at least the WhisperX CPU path with one alternative. Measure model download/storage, cold startup, warm processing, peak memory, coverage and human-inspected boundary error. Record the actual Mac model and exact dependency versions; no universal latency claim before this measurement.
2. **Worker and artifacts.** Implement one optional alignment adapter, durable per-asset reuse, cancellation/restart recovery and recorded failures. Preserve source/audio and both listening modes. No automatic cloud fallback and no automatic regeneration of paid audio when alignment fails.
3. **Reader integration.** Serve validated spans, highlight from the media clock, retain passage fallback, and handle seeking, 1×/2×/2.5×, mode changes, background tabs and screen-reader behavior. Keep highlighting updates cheap and avoid re-rendering an entire chapter per frame.
4. **Quality and export.** Add independent transcript verification where useful, pronunciation mappings and portable timing export. Only claim word accuracy supported by a reviewed sample and stated error/coverage results.

Mock tests can establish source preservation, caching, retry limits and player state transitions. They cannot establish acoustic accuracy, acting quality or Apple Silicon throughput. Those require a bounded real-audio benchmark before making word highlighting the default.
