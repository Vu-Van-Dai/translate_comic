---
name: token-savings
description: 2026-10-20 token-cost work -- what changed (QA rounds show only fixed pages, centre/spill checks inside comic render, CLAUDE.md split into docs/), what is planned or dropped, and the operator's Opus High -> Medium workflow for a series
metadata:
  type: project
---

The operator (2026-10-16..20) asked to cut Claude's token cost per chapter **without losing quality**. Branch: `claude/translation-cost-optimization-mm6y2f`.

## Done (2026-10-20)

1. **visual_qa round 2+ shows only pages not already approved.** Approved = shown, not fixed, not in `blocked`, no error-level pre-check left unanswered. Each shown page is fingerprinted (source, mask, analysis, its Vietnamese/voices, overrides, fonts profile, font files, LaMa model, rendering code); an approved page whose fingerprint still matches renders pixel-identical, so it is listed in `unchanged_pages` and not shown again. Everything else is re-rendered: fixed/blocked/error pages, pages never shown this cycle, pages whose translation or profile changed. `passed=true` publishes all pages and clears the approvals (next render = fresh cycle, every page). Accepts-only answers show the whole chapter. After `retranslate_shorter`, the next `comic render` renders only non-approved pages; `--all` renders everything. First version (fixed same day after an adversarial review) inferred approval from "no fix touched it" and missed blocked pages, overlooked errors, translation/profile edits and partial previews. A second review found 8 more edge cases (reverted trial renders, edits during a render, typo region ids, 'pages 3 and 4' in blocked, a reused answer file, passed=true with unrendered fixes after a -p preview, code outside typesetting/); all fixed: approval now also requires the rendered file's own hash, the answer file is renamed after use, unknown ids are refused, pending pages are shown before publishing. Verified on a synthetic chapter, all those scenarios included: untouched pages stay byte-identical and `--all` reproduces every page byte for byte.
2. **Centre and spill checks run inside `comic render`.** Same measurements as `scripts/centre_check.py` / `scripts/spill_check.py` (moved to `comic/qa/placement.py`; the scripts are thin wrappers with identical output, verified old vs new). Findings reach the visual_qa request as `off_centre` / `spill` **warnings** (false alarms on dot tone, SFX, shared bubbles -- judge by eye). No more running the two scripts by hand. Side fix: a region with several findings now keeps all of them (the request used to keep only the last check per region, so a spill could have hidden an overflow). Adds ~0.5-1 s per page to rendering.
3. **CLAUDE.md split.** Everyday rules stay in CLAUDE.md (~21 KB, was ~63 KB). Moved word for word: Photoshop route -> `docs/photoshop.md`, videos -> `docs/video.md`, downloading/capture/import/sites -> `docs/acquisition.md`, OCR/detection/webtoon slicing -> `docs/ocr-and-detection.md`. A "Read before you..." list at the top of CLAUDE.md says which file to read first; `comic ps ...` and `comic video ...` print a one-line reminder of their file. Corrected: the margin paragraph (the manga/manhwa presets set `pad_em 0.6`, `size_scale 0.9`; Mi Imitador and conan keep the old rule on purpose and will not be redone).

## The operator's workflow for a series (2026-10-20, verbatim intent)

1. Translate the **first 2-3 chapters with Opus at High effort**, to build the character profiles, pronoun pairs and glossary carefully. **Skip all SFX in this phase** (dialogue, captions, narration only; no `comic ps sfx` pass yet).
2. Then **stop and ask the operator to switch to Opus Medium**; do not continue until they confirm. (Claude cannot change its own model/effort.)
3. At Medium, **finish the whole series**: translate every remaining chapter, then the SFX pass and the Photoshop route (`comic ps sfx` -> `comic ps build` -> `scripts/leftover_scan.py` -> `comic ps publish`) for **every** chapter, including the first 2-3 -- the entire project finished.

**Never switch model or effort in the middle of a conversation** (operator, 2026-10-21) -- the switch happens between conversations, at the step-2 stop.

Also: Opus High for code changes to `comic/`. When the operator drives the gates by hand, one gate per conversation (`/clear` after each `comic resume`).

## Report bugs checked in practice (2026-10-21)

Operator: fix them if real, my call, skip only what costs more than it saves. Token-saving ideas: **not to be implemented** (operator, 2026-10-21) unless I argue otherwise.
- #5 overlay numbers: real -> labels now use the region id's number (`vision/analyze.py`).
- #11 retranslate wipes speaker/addressee/note/voice: real -> fields left out of an answer keep their earlier value (`translation/gate.py`, `model_fields_set`).
- #14 `analyze --force` wipes review: real -> `stages._keep_review` keeps reviewed readings and drops when the new mask is identical.
- #7(c) `kind=sfx` regions never offered at the SFX gate: real (e.g. Itsuka ch.4 "DASH") -> `dropped_regions` lists them.
- #6 leftover_scan misses English SFX: real -> onset/final cluster rule catches THUD/SLAM/GASP/DRAG/GRAB/STOMP; dropped the/me/no/to/oh (accent-stripped Vietnamese). A "not a Vietnamese syllable" rule was tried and dropped: 16 -> 55 hits on Kimigazer ch.1, all signs/misreads.
- #17 LaMa: BGR feed real (tiny effect, <1/255 on a Dasadanan page) -> fixed; window now covers every glyph pixel. Found while testing: overlapping balloons had the model copy a neighbour's letters into the hole (Itsuka ch.6 p.12, 16% residue) -> `clean_page` hides all lettering from the model and paints from the cleaned page; residue 0.
- #21 >2000 px pages downscaled in long conversations: not acted on (manga pages are ~1400 px; revisit for webtoons).
- Claude Cloud's QA-round and in-render checks tested on Itsuka ch.6: round 2 rendered only the fixed page, checks match the scripts. Kept.

## Planned, not built yet

- `scripts/session_cost.py`: read Claude Code's transcripts (`%USERPROFILE%\.claude\projects\e--Sefl-Study-tranlates-comic-project\*.jsonl`), dedupe by response id, attribute usage to series/chapter/gate from the `comic` commands and "GATE:" banners, count images read, print per-gate totals (input, cache write, cache read, output, thinking, full-price equivalent). Purpose: measure before/after instead of estimating.
- Optional, only on the operator's yes: report the bubble's real room (bubble minus the series' margin, from `layout_region`) as `fit_width`/`fit_height` at the translate gate -- today they are the English text box, so budgets understate the room; allow 3-digit region numbers in SFX ids (`^p\d{3}_r\d{2}$` in `photoshop/sfx.py` rejects r100+).
- Open checks: the API's stricter per-image size limit once a conversation holds >20 images (pages up to 2600 px tall from the slicer); whether translating fewer pages per conversation (`comic translate -p`) helps -- test on a finished chapter first.

- Research report with 21 further ideas and 7 suspected bugs (unverified, nothing decided): `docs/research/token-savings-2026-10-20.md`.

## Dropped -- do not re-propose

Compact table instead of the translate JSON (little saving, format risk); TSV translate answers (a column shift fails silently and could swap speaker/addressee into the pinned pronoun table); contact sheets for dialogue or SFX crops, or telling Claude to open many crops at once (legibility, id mix-ups, no measured basis); showing the original page at visual_qa only when flagged (smudges and missed lines get through -- the operator catches those); merging ocr_review into translate; Photoshop Generative Fill / Adobe AI (cracked Photoshop, cloud upload). Removing the "per-region crops show the text itself" instruction -- crops are where confident OCR mistakes and cut-off phrases are caught.
