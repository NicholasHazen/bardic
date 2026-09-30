# UI guide

The look is "Aurora glass". The canvas (`design/canvas/`, images in `design/boards/`) is the reference; the `Main` (foundations), `Components`, `B1Palette` and `Vocab` boards show these rules in use.

## Colour from the cover
Each book's screens take their colour from the book's cover. The server stores a measured sample (`Cover.sample`: hex, hue, saturation, lightness, `vivid`); the client derives the theme from it.

| Token | Rule |
|---|---|
| base | the cover hue at about 7.5% lightness |
| glow 1 | the hue at about 55% lightness, blurred large, behind content |
| glow 2 | hue +40° at about 50% lightness |
| accent | the hue lifted in lightness until it reaches at least 7:1 against the base |
| fallback | when there is no sample or `vivid` is false, use the default coral/amber palette |

Text is ink `#f5f1ea`; muted text `#d0cade` (at least 4.5:1 on the base). The accent is the only saturated colour on a screen; status tones (good, info, warn, bad) are used for badges and callouts only.

## Type
Bricolage Grotesque for interface, titles and numbers. Newsreader (serif) for book text and chapter titles. Reading size 19 to 23 at about 1.7 line height. Interface sizes 11, 12, 13, 14, 15, 17, 24, 34, 38.

## Glass, in three tiers
| Tier | Use | Fill | Blur |
|---|---|---|---|
| Control | buttons, chips, small cards | 9% white, 1px edge at 18% | 20 to 26 |
| Panel | anything text sits on | 50% base | 30 |
| Floating | sheets, player bar, popovers | 60 to 90% base, strong shadow | 30 to 40 |

## Shape and size
Pill for controls under 56 px tall. Cards 16 to 20, sheets 32, rows 12. Covers 6 to 10. Nested corners are concentric: inner radius = outer radius minus the inset. Touch targets are at least 44 px; the primary action is 52 px; play is larger still. Space scale 4, 8, 12, 16, 20, 24, 32.

## Layout
Phone: portrait only, three tabs (Home, Library, Settings), a compact player bar above the tab bar, sheets from the bottom. Tablet: a rail on the left; Now Playing splits Listen on the left and Read on the right in landscape, and uses a Listen/Read switch in portrait.

## Status words
| Kind | Words |
|---|---|
| Audio (per chapter) | Ready, On this device, Making, Not yet; on a device also Downloading, Couldn't download, Out of date |
| Listening | Playing, Getting ready, Waiting, Needs you |
| Money | Free, Premium, Estimate, Limit (per plan), Allowance (monthly) |

## Patterns
- **Nothing paid without a plan sheet:** scope, text size, chapters to make, time, estimate as a range with a most-likely value, the plan's limit, what is left under a monthly limit if one is set, and "Why a range?".
- **Every problem says what is kept** first.
- **Read mode never grows:** controls are one capsule; a download ring appears beside the reader controls and never covers text.
- **Places:** when two places differ, show both with device, chapter, progress and time.
- **Unknown stays unknown:** never display a missing cost or size as zero.

## Copy rules
"Your Bardic computer" is the server (not "home", "the cloud" or "this computer"). "This device" is the device in hand. "Allowance" only for the monthly ceiling. Say what a button does ("Make ready", "Download", "Plan the whole book").
