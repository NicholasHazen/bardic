# Source, analysis, and synchronization research

Research checked 27 September 2026. The recommendations below distinguish implemented behavior from later extensions.

## Source fidelity and ebook structure

The original uploaded file is kept by the application. Import produces immutable chapter text and exact Python Unicode code-point spans. The browser renders those passages by ID rather than slicing JavaScript strings, avoiding the Unicode-code-point versus UTF-16 offset mismatch.

TXT import accepts UTF-8, normalizes line endings, and identifies English chapter/part/prologue/epilogue headings. EPUB import follows the OPF spine, parses XML with entity expansion disabled, extracts text from XHTML blocks, ignores scripting/navigation content, and retains paragraph boundaries. Explicit scene ornaments and `<hr>` become whitespace plus scene boundaries. This is a reading-text representation, not a promise to reproduce the ebook's original visual layout. The import tests verify every non-whitespace canonical character is covered by an ordered passage and each passage exactly matches its source span.

EPUB ZIP limits, path validation, and safe XML parsing apply before analysis. Encrypted reading content is rejected; the importer never removes DRM. Recognized font obfuscation can be ignored because the plain-text reader does not need those font files. Fixed-layout/image-only and malformed HTML books may require conversion to a supported text EPUB or TXT.

The EPUB specification defines default reading order through the spine and offers Media Overlays to associate audio timings with text fragments. Later export can add SMIL and marked-up passage IDs while retaining the ordinary EPUB text. [W3C EPUB 3.3](https://www.w3.org/TR/epub-33/)

Readium's locator model can carry text context, DOM ranges, CSS selectors, and partial CFIs if faithful EPUB rendering or portable annotations become a later goal. [Readium HTML locators](https://readium.org/architecture/models/locators/extensions/html.html)

## Cast and performance analysis

The deterministic local draft recognizes explicit named speech attributions and a small set of delivery cues. It deliberately leaves pronouns and ambiguous dialogue unresolved. It detects structural scenes but does not claim literary understanding. A capitalized word alone is insufficient to create a character. The original demonstration is 343 words over two scenes, with ambiguous lines left available for review.

Quote scanning recognizes straight and typographic quotation marks, contractions inside single-quoted dialogue, and conventional multi-paragraph speeches with a repeated opening mark. Ambiguous scare quotes, dialect elisions, and apostrophes can still require review. Local speech tags support common past/present forms, honorifics, and apostrophized/hyphenated names. Negated, nervous, or humorless laughter never automatically becomes a smiling performance.

Gemini analysis is a three-stage process:

1. Discover character candidates in bounded source batches, with exact supporting quotations.
2. Aggregate exact names and reconcile aliases and stable vocal profiles using evidence from across the book.
3. Annotate bounded scene batches by exact passage IDs, including speaker, confidence, local performance direction, emotional cues, scene summaries, and optional additional scene boundaries.

Book content is treated as data, not instructions. Model output never becomes authoritative book text. Responses are validated for known IDs, one annotation per passage, existing speakers, numerical confidence, exact evidence quotations, and valid boundaries. Attributions below 65% confidence remain unresolved. Evidence validation proves a quotation occurs in the source; it does not prove the model's interpretation is correct. Cast and scene review remain part of the workflow.

The model may infer how to perform a passage, but explicit claims about accents, vocal traits, and aliases should be grounded in the book. Stable character profiles are separate from temporary scene emotions. Narration and dialogue tags remain in the source and are spoken; directions do not authorize rewriting or abridgment. All cloud analysis remains a draft. Manual edits are retained through the `edited` flag, and existing character voice choices remain stable on re-analysis.

Scene batches include bounded neighboring source context for attribution across a batch boundary. Global reconciliation must account for every discovered name. Explicit global aliases can consolidate duplicate automatic draft characters, while separately reviewed identities are never silently merged. Gemini can improve automatic character directions; manually reviewed directions remain unchanged.

Google supports structured JSON responses but recommends validating values in application code because schema compliance is not semantic correctness. [Gemini structured outputs](https://ai.google.dev/gemini-api/docs/structured-output)

## Passage synchronization now; word alignment later

The implemented reader uses one source passage per generated clip. Its highlighted passage is the exact text assigned to that clip. This establishes reliable clip-level boundaries, but does not verify that a generative speech model spoke every word accurately. Clips should be listened to before treating a production as finished. There are no invented word timestamps.

For a future word-alignment stage, align each known source transcript to its own clip, then offset local word times into the chapter timeline. Keep the timing method explicit and fall back to passage highlighting when alignment fails. An independent transcription comparison should detect omitted, repeated, or substituted words before accepting an alignment.

| Candidate | Relevant capability | Tradeoff |
| --- | --- | --- |
| stable-ts | Direct alignment of supplied text to audio; `align_words` can constrain word times inside existing segment bounds. Also documents MLX transcription on Apple Silicon. | Verify the selected alignment backend on the target Mac; MLX transcription support does not imply every alignment operation runs through MLX. |
| WhisperX | Word alignment with language-specific phoneme models; explicitly documents CPU usage for macOS. | Additional models and dependencies; some numerals and dictionary-missing characters may not receive timestamps. |
| Montreal Forced Aligner | Acoustic/dictionary-based forced alignment. | Conda/Kaldi/OpenFst installation adds complexity for a small local app. |

Sources: [stable-ts documentation](https://github.com/jianfch/stable-ts), [WhisperX documentation](https://github.com/m-bain/whisperX), [Montreal Forced Aligner installation](https://montreal-forced-aligner.readthedocs.io/en/latest/installation.html).

## Local durability

The application uses SQLite state and separately stored audio. A render job checkpoints successful clips, allowing interrupted work to resume using cached takes. Cache identity includes the source and effective performance settings. Re-analysis and manual changes invalidate obsolete audio associations. Keep original inputs, production metadata, settings revisions, and output files separate so a book can be re-cast without re-importing its prose.

SQLite provides transaction atomicity, and its WAL mode supports the local state-management use case. Long network generation should occur outside a database write transaction. [SQLite atomic commit](https://www.sqlite.org/atomiccommit.html), [SQLite WAL](https://www.sqlite.org/wal.html).

The interface, library, book files, and generated audio are local. Selecting Gemini analysis or narration sends the required excerpts and production notes to Google's API with the user's key. The local draft and macOS speech provider offer a path without those cloud requests.
