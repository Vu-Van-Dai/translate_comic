---
name: token-savings
description: 2026-10-20 token-cost work -- what changed (QA rounds show only fixed pages, centre/spill checks inside comic render, CLAUDE.md split into docs/), what is planned or dropped, and the operator's Opus High -> Medium workflow for a series
metadata:
  type: project
---

The operator (2026-10-16..20) asked to cut Claude's token cost per chapter **without losing quality**. Branch: `claude/translation-cost-optimization-mm6y2f`.

## Done (2026-10-20)

1. **visual_qa round 2+ shows only pages not already approved.** Approved = shown, not fixed, not in `blocked`, no error-level pre-check left unanswered. Each shown page is fingerprinted (source, mask, analysis, its Vietnamese/voices, overrides, fonts profile, font files, LaMa model, rendering code); an approved page whose fingerprint still matches renders pixel-identical, so it is listed in `unchanged_pages` and not shown again. Everything else is re-rendered: fixed/blocked/error pages, pages never shown this cycle, pages whose translation or profile changed. `passed=true` publishes all pages and clears the approvals (next render = fresh cycle, every page). Accepts-only answers show the whole chapter. After `retranslate_shorter`, the next `comic render` renders only non-approved pages; `--all` renders everything. First version (fixed same day after an adversarial review) inferred approval from "no fix touched it" and missed blocked pages, overlooked errors, translation/profile edits and partial previews. Verified on a synthetic chapter, all those scenarios included: untouched pages stay byte-identical and `--all` reproduces every page byte for byte.
2. **Centre and spill checks run inside `comic render`.** Same measurements as `scripts/centre_check.py` / `scripts/spill_check.py` (moved to `comic/qa/placement.py`; the scripts are thin wrappers with identical output, verified old vs new). Findings reach the visual_qa request as `off_centre` / `spill` **warnings** (false alarms on dot tone, SFX, shared bubbles -- judge by eye). No more running the two scripts by hand. Side fix: a region with several findings now keeps all of them (the request used to keep only the last check per region, so a spill could have hidden an overflow). Adds ~0.5-1 s per page to rendering.
3. **CLAUDE.md split.** Everyday rules stay in CLAUDE.md (~21 KB, was ~63 KB). Moved word for word: Photoshop route -> `docs/photoshop.md`, videos -> `docs/video.md`, downloading/capture/import/sites -> `docs/acquisition.md`, OCR/detection/webtoon slicing -> `docs/ocr-and-detection.md`. A "Read before you..." list at the top of CLAUDE.md says which file to read first; `comic ps ...` and `comic video ...` print a one-line reminder of their file. Corrected: the margin paragraph (the manga/manhwa presets set `pad_em 0.6`, `size_scale 0.9`; Mi Imitador and conan keep the old rule on purpose and will not be redone).

## The operator's workflow for a series (2026-10-20, verbatim intent)

1. Translate the **first 2-3 chapters with Opus at High effort**, to build the character profiles, pronoun pairs and glossary carefully. **Skip all SFX in this phase** (dialogue, captions, narration only; no `comic ps sfx` pass yet).
2. Then **stop and ask the operator to switch to Opus Medium**; do not continue until they confirm. (Claude cannot change its own model/effort.)
3. At Medium, **finish the whole series**: translate every remaining chapter, then the SFX pass and the Photoshop route (`comic ps sfx` -> `comic ps build` -> `scripts/leftover_scan.py` -> `comic ps publish`) for **every** chapter, including the first 2-3 -- the entire project finished.

Also: Opus High for code changes to `comic/`. When the operator drives the gates by hand, one gate per conversation (`/clear` after each `comic resume`).

## Planned, not built yet

- `scripts/session_cost.py`: read Claude Code's transcripts (`%USERPROFILE%\.claude\projects\e--Sefl-Study-tranlates-comic-project\*.jsonl`), dedupe by response id, attribute usage to series/chapter/gate from the `comic` commands and "GATE:" banners, count images read, print per-gate totals (input, cache write, cache read, output, thinking, full-price equivalent). Purpose: measure before/after instead of estimating.
- Optional, only on the operator's yes: report the bubble's real room (bubble minus the series' margin, from `layout_region`) as `fit_width`/`fit_height` at the translate gate -- today they are the English text box, so budgets understate the room; allow 3-digit region numbers in SFX ids (`^p\d{3}_r\d{2}$` in `photoshop/sfx.py` rejects r100+).
- Open checks: the API's stricter per-image size limit once a conversation holds >20 images (pages up to 2600 px tall from the slicer); whether translating fewer pages per conversation (`comic translate -p`) helps -- test on a finished chapter first.

## Dropped -- do not re-propose

Compact table instead of the translate JSON (little saving, format risk); TSV translate answers (a column shift fails silently and could swap speaker/addressee into the pinned pronoun table); contact sheets for dialogue or SFX crops, or telling Claude to open many crops at once (legibility, id mix-ups, no measured basis); showing the original page at visual_qa only when flagged (smudges and missed lines get through -- the operator catches those); merging ocr_review into translate; Photoshop Generative Fill / Adobe AI (cracked Photoshop, cloud upload). Removing the "per-region crops show the text itself" instruction -- crops are where confident OCR mistakes and cut-off phrases are caught.
