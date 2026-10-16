---
name: mistakes-log
description: Running log of Claude's own mistakes on this project and the rule each one taught — read before starting any chapter
metadata:
  type: feedback
---

The user asked (2026-10-13) for a standing record of my mistakes so they are not repeated. Add a dated line whenever the user catches something, or I do.

**Process**
- 2026-10-12 Published Ch.1's plain pipeline render to library/ as if finished. Rule: done = Photoshop route + full-page review; see [[super-lovers-finish]].
- 2026-10-12 Reviewed only the pre-check list, not every bubble. User found spills, empty bubbles, off-centre text. Rule: look at every page at full size; run `scripts/spill_check.py` and `scripts/leftover_scan.py` before export.
- 2026-10-12 Sent progress updates per step. Rule: [[work-autonomously]].
- 2026-10-12 A broken helper (write_json on a dict) silently failed and I re-rendered anyway. Rule: check every step's output before building on it.

**Typesetting**
- 2026-10-12 Used `voice` faces (angry/menacing/soft…) in Ch.1: mixed faces, scratchy unreadable text over dot tone. Rule: no voices for Super Lovers.
- 2026-10-13 Fit areas leaked: the tone search beat the bubble search on plain white bubbles (4x–14x too big) → a line set above an empty bubble (p.14), two bubbles merged (p.23, p.38), text off-centre. Fixed in `mask.fit_areas` (tone search only when letters sit on tone).
- 2026-10-13 Two touching balloons detected as one region were set as one block. Rule: split them with two ps items (`replaces`).
- 2026-10-13 Augie font has 25%-too-wide accented letters ("nhó c", "KÉ T") — the coverage gate does not check spacing. Fixed as `000AugieVN-TB.ttf`. Rule: check advance widths of any new face (`scripts/fix_font_advances.py`); a fixed font needs a new name or Photoshop keeps cached widths.

- 2026-10-15 Text sat off-centre in many bubbles: the fitter centred on the scanlation's text box and maximised size before centring. Fixed (`fit._bubble_centre`, `fit._centred`); rule: run `scripts/centre_check.py` on every chapter.
- 2026-10-15 Captions in thin Augie read pale/grey on small scans. Manga preset now uses Felt-TB; rule: judge a face's weight at the real page size, not only its coverage.
- 2026-10-15 Ch.4: gave Nishiki's lines (long blond hair, "Rapunzel") to Erika. Rule: identify speakers from a character's established look, check against the previous chapter's pages, not from a summary note.

**Cleaning**
- 2026-10-12 Built with `--clean content-aware`: CAF dirtied white bubbles. Rule: pipeline erase for dialogue, CAF for SFX only.
- 2026-10-12 Told the user LaMa/CPU facts without checking them first, and argued instead of testing. Rule: measure on the real page before claiming which method is better.
- 2026-10-13 Scanlator "SFX: …" labels set as kind=caption → grey smeared boxes. Rule: they are SFX items.
- 2026-10-13 Source quote marks outside the detector mask stayed on the page (p.26). Rule: check punctuation around every caption.

- 2026-10-13 LaMa left green/red specks on black-and-white pages (p.16, p.31). Fixed: `clean._inpaint` returns grey when the surroundings are grey. Rule: run the new-colour check on grey series before export.
- 2026-10-13 Reused an item id (p039_r90) for a new ps item and silently overwrote the "SFX: SLIP" label. Rule: check for duplicate region_ids after editing sfx_response.json.
- 2026-10-13 Detector boxes can cover only part of a phrase ("It's" of "It's mine!", p.33) and the rest stays English. Rule: compare each region's box with the whole lettering on the review crops.

- 2026-10-13 Ch.1 final: the user still found small alignment issues and merged dialogue after my full review (they fix Ch.1 by hand). Rule: on every page check each bubble's text is centred in *its own* balloon and no two balloons share one block — spill_check alone does not catch it.

- 2026-10-13 Ch.2: replacing a region with Photoshop items left the pipeline's old erase in place where no item reached (p.36 shop sign, p.10 phone lines); `plan.py` now restores the original under every superseded region first. A ps item with the wrong bbox left English on the page (p.23) -- always run `scripts/leftover_scan.py` after the build.

**Environment**
- 2026-10-13 Spent many attempts on per-user font install / cache tricks before looking up how Photoshop loads fonts. Rule: put faces in `C:\Program Files\Common Files\Adobe\Fonts` (elevated copy); search docs early when a tool misbehaves.
- 2026-10-12 Reinstall swapped in CPU-only onnxruntime; pipeline pinned the CPU at 100%. Rule: [[cpu-limit]], doctor checks DirectML.

**Video**
- 2026-10-14 First video pilot scored 2/10: I designed styles from 10 clips by measuring shot lengths, then chose library music, tight face crops and captions on every shot -- none of which the reference channels do. Rule: copy what real BL edits actually do (full panels, trending song, no on-screen text) before inventing; judge against a real template, not my own checklist.
