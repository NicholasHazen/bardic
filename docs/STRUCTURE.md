# EPUB structure and metadata repair

The EPUB spine defines reading order, not chapter numbering. Bardic preserves one immutable source unit per readable linear spine document. Its displayed title and section kind are determined locally, without a model request:

1. A valid EPUB 3 table-of-contents navigation link and its target anchor.
2. Legacy NCX navigation when the EPUB 3 TOC is unavailable or has no valid targets.
3. Content headings, structural semantics, or recognizable landmark labels.
4. A neutral section label when no reliable title exists.

The importer ignores page-list navigation for chapter naming. It keeps spine order even if TOC links are listed out of order. Missing anchors, unsafe paths, remote links, and malformed optional navigation do not replace otherwise readable source text. EPUB content still receives the existing archive, encryption, XML, and path safety checks.

Each source unit records `source_href`, `title_source`, `kind`, and `logical_sections`. Kinds distinguish chapters, recaps, front matter, back matter, and other sections. `narrative_order` counts recognized narrative chapters independently of storage order. Unlabeled units before the first explicit chapter or recap receive a conservative front-matter label; unknown body sections remain sections.

When a document contains several TOC targets, the logical-section entries retain exact extracted-text start/end offsets, labels, and nesting depth. The source unit stays intact and receives a neutral container label. Execution across individual logical subranges is a future refinement: the current pipeline still processes the source unit. No generated heading is inserted into the original prose.

`repair_structure(book, filename, data)` reparses the original source and verifies every source unit's text and order before returning a metadata-only copy. A mismatch stops the repair. Existing chapter, passage and scene IDs, source offsets, audio, character observations, and reviewed scene titles remain intact. Generic scene labels follow the corrected chapter title. `transform_checkpoint_structure(checkpoint, updated_book)` applies the same metadata to a checkpoint baseline and its progress rows; the caller must recompute the fingerprint and save the book/checkpoint atomically.

Read-only validation against the first real EPUB found 42 text-bearing source units: 3 front-matter sections, a recap, 36 chapters, and 2 back-matter sections. The fourth imported unit is **The Story Thus Far**, and the fifth is **Chapter 1**. The repair preserved all 656,638 source characters, 7,299 passages, 8 accepted checkpoint units, and 855 character references.

Primary references: [EPUB spine](https://www.w3.org/TR/epub-33/#sec-spine-elem), [TOC navigation](https://www.w3.org/TR/epub-33/#sec-nav-toc), [landmarks](https://www.w3.org/TR/epub-33/#sec-nav-landmarks), [structural semantics](https://www.w3.org/TR/epub-ssv-11/).
