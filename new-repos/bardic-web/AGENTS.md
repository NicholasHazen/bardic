# Working on the Bardic web client

Entry point for coding agents. User instructions for the current task take precedence over this file.

## Read before changing code
1. `docs/PRODUCT-SPEC.md` (promises, requirements, acceptance tests) and `docs/UI-GUIDE.md`.
2. `design/README.md` and the board images for the screen you are building. Match the board; if you must deviate, say why.
3. `contract/openapi.yaml`: the generated client types come from it. If the contract needs to change, change it in the server repository first.
4. `docs/ARCHITECTURE.md` for structure; `docs/ROADMAP.md` for scope.

## Rules
- **Words are never changed.** Render the exact chapter text from the server. Offsets are Unicode code points, not JavaScript string indices; use code point aware helpers when mapping offsets to DOM text.
- **No paid action without the plan flow.** The client never calls an operation with cost class `may_charge` for a premium audiobook unless the listener approved a plan in this flow. Show the estimate range, the limit and what is kept; unknown is shown as unknown.
- **One word per idea.** Use the audio words (Ready, On this device, Making, Not yet; Downloading, Couldn't download, Out of date) and the four listening states (Playing, Getting ready, Waiting, Needs you). Do not invent labels. See `design/` Vocab board.
- **Places sync through revisions.** Always send `base_revision`; on `place_conflict`, apply the listener's setting and never discard the other place silently.
- **Offline is first class.** Downloaded chapters play and read without the server. Never replace a downloaded chapter without the listener's choice.
- **Accessibility.** Touch targets at least 44 px, text contrast at least 4.5:1, real buttons and labels, names on icon buttons, reduced motion respected. Phones portrait only.
- **Secrets.** The client never stores provider keys; it sends them once to the server and never reads them back.
- Use original synthetic text in tests and fixtures; never commit a user's books or audio.

## Workflow
Keep changes scoped, update the docs when behaviour or a screen changes, run the type check, lint and tests, and report what was verified.
