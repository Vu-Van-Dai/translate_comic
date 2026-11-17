---
name: mistakes-log
description: Running log of Claude's own mistakes on this project and the rule each one taught — read before starting any chapter
metadata:
  type: feedback
---

The user asked (2026-10-13) for a standing record of my mistakes so they are not repeated. Add a dated line whenever the user catches something, or I do.

**Process**
- 2026-10-12 Published Ch.1's plain pipeline render to library/ as if finished. Rule: done = Photoshop route + full-page review; see [[super-lovers-finish]].
- 2026-10-12 Reviewed only the pre-check list, not every bubble. User found spills, empty bubbles, off-centre text. Rule: look at every page at full size; check the `spill` / `off_centre` pre-checks (since 2026-10-20 `comic render` runs both checks itself) and run `scripts/leftover_scan.py` before export.
- 2026-10-12 Sent progress updates per step. Rule: [[work-autonomously]].
- 2026-10-12 A broken helper (write_json on a dict) silently failed and I re-rendered anyway. Rule: check every step's output before building on it.

**Typesetting**
- 2026-10-12 Used `voice` faces (angry/menacing/soft…) in Ch.1: mixed faces, scratchy unreadable text over dot tone. Rule: no voices for Super Lovers.
- 2026-10-13 Fit areas leaked: the tone search beat the bubble search on plain white bubbles (4x–14x too big) → a line set above an empty bubble (p.14), two bubbles merged (p.23, p.38), text off-centre. Fixed in `mask.fit_areas` (tone search only when letters sit on tone).
- 2026-10-13 Two touching balloons detected as one region were set as one block. Rule: split them with two ps items (`replaces`).
- 2026-10-13 Augie font has 25%-too-wide accented letters ("nhó c", "KÉ T") — the coverage gate does not check spacing. Fixed as `000AugieVN-TB.ttf`. Rule: check advance widths of any new face (`scripts/fix_font_advances.py`); a fixed font needs a new name or Photoshop keeps cached widths.

- 2026-10-15 Text sat off-centre in many bubbles: the fitter centred on the scanlation's text box and maximised size before centring. Fixed (`fit._bubble_centre`, `fit._centred`); rule: check the centring of every chapter -- since 2026-10-20 `comic render` runs the check on every page and lists `off_centre` warnings in the visual_qa request; judge each by eye.
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

- 2026-10-13 Ch.1 final: the user still found small alignment issues and merged dialogue after my full review (they fix Ch.1 by hand). Rule: on every page check each bubble's text is centred in *its own* balloon and no two balloons share one block — the spill check alone does not catch it.

- 2026-10-13 Ch.2: replacing a region with Photoshop items left the pipeline's old erase in place where no item reached (p.36 shop sign, p.10 phone lines); `plan.py` now restores the original under every superseded region first. A ps item with the wrong bbox left English on the page (p.23) -- always run `scripts/leftover_scan.py` after the build.

**Environment**
- 2026-10-13 Spent many attempts on per-user font install / cache tricks before looking up how Photoshop loads fonts. Rule: put faces in `C:\Program Files\Common Files\Adobe\Fonts` (elevated copy); search docs early when a tool misbehaves.
- 2026-10-12 Reinstall swapped in CPU-only onnxruntime; pipeline pinned the CPU at 100%. Rule: [[cpu-limit]], doctor checks DirectML.

**Video**
- 2026-10-14 First video pilot scored 2/10: I designed styles from 10 clips by measuring shot lengths, then chose library music, tight face crops and captions on every shot -- none of which the reference channels do. Rule: copy what real BL edits actually do (full panels, trending song, no on-screen text) before inventing; judge against a real template, not my own checklist.

- 2026-10-22 CapCut template kits (capcut.com/vi-vn/template) failed: I read the slot count off the page and assumed N different images, but e.g. "Đợi đến lúc anh biết" repeats ONE image 4 times with different effects; in the other templates the music/effects did not work at all once filled; the templates and their music do not suit comics. User: "unreliable... even worse than the stuff you make yourself". Rule: never recommend a template I have not actually opened and tested end to end (structure of each slot, whether the audio/effects survive); generic trending templates are not a fit for manga -- don't pick them by title/view count.

- 2026-10-23 visual_qa: answered passed=true with no fixes while pages had ink_residue errors -> those pages stayed unapproved and the gate re-showed them (looped twice, ~20 min of renders). Rule: every error-level pre-check must get a fix or an explicit `accept` entry in the answer, even when passing.

**Acquisition**
- 2026-10-26 Started a capture with a trailing `&` in a plain Bash call, thought it had not run, started it again: two jobs drove the project Edge at once and one chapter came out with 15 of 42 pages. Rule: long jobs only via run_in_background, one Edge job at a time, and count every chapter's pages afterwards.
- 2026-10-26 Treated ~700-byte images as failed downloads for hours of debugging; they were blank spacer strips. Rule: print the size/type of a "missing" item before building workarounds.
- 2026-10-26 comix_fetch's "N pages, 0 missing" only meant every *collected* image had bytes: an internet drop logged a 7-page chapter as complete, and the reader's comment section (user pictures, 512 px wide) was saved as extra pages in 32/47 MID chapters and all of Dare mo -- one comment picture even sat before the last real page. Rule: check every comix chapter against the reader's own "k / N" counter with `workspace/_tools/comix_verify.py ... --trim` (drops narrower images, renumbers, acts only if exactly N remain), and import only after it says OK.

**Translation tooling**
- 2026-10-30 build_resp.py's sentence-case pass capitalised `_proper` names as substrings and as common words: Kawaii heroine "Ai" turned "trai" into "trAi" and the pronoun "ai" (who/anyone) into "Ai" on 10 published-bound lines. Fixed (word boundaries) and the lines corrected. Rule: never put a name that is also a Vietnamese word (Ai, Ren?...) in `_proper`; after building a translate response, grep sentence-case lines for mid-word or mid-sentence capitals.
- 2026-10-31 NH ch.1: dropped two real speech lines as "sfx" at ocr_review ("EH-" p.29, "HOW HAS YOUR DAY BEEN?" p.30) -- the region dump listed them only as ids. Rule: never DROP an id range blindly; every region with a readable engine_text must be looked at on the overview before it is dropped.
- 2026-10-31 NH ch.6: fragment pieces of split bubbles were left out of the notes, so gate_run DROPped them and their English stayed on the page (p.14, 25, 26, 27). Rule: in fragmented chapters every visible fragment id goes in the notes as ERASE; at QA fix leftovers with a manual erase region (scratchpad addbox.py: kind erase, id r91+, in analysis/page_NNN.json).
- 2026-10-31 NH ch.7: wrote the whole chapter's notes against overview sheets whose page labels were off by two, so every line went to the wrong page. Rule: before writing notes, confirm one page's sheet label against original/NNN.png (ov.py labels from the filename).
- 2026-11-01 MID ch.7 p50-62: attributed the demon's lines to Wonyoung (wrong pronouns cậu/tôi instead of ngươi/ta). Rule: identify the speaker from the ART (who is in the panel), not from the previous scene; check every "Wy" line against a face.
- 2026-11-10: user flagged that my cleaning often smears dot screentone behind text -> pattern-aware fill + visual check of tone regions (see clean-screentone.md)

- 2026-11-13: user said "make full use of the T4's VRAM" (meaning: faster + cleaner LaMa erasing). I moved the BUBBLE
  model to the T4 instead -- against the standing rule Colab = LaMa only ([[colab-only-lama]]). Reverted before it ran.
  Rule: "use the T4/VRAM" = improve LaMa (batching, full resolution, refinement); never move detection/OCR/bubbles there.
- 2026-11-13: user found site/group marks left on JJ, Kawaii, KKLNY after "re-clean done". Causes: JJ ch.1/2 never had
  mark boxes (no Studio review); JJ 5/5.5 + Kawaii 1-3 boxes smaller than the mark (jmcomic reaches y 870-925, boxes
  stopped at 913 -> LaMa rebuilt ghost letters); the KKLNY blue group mark (太想寵愛...霧欠) labelled sfx/narration
  (so translated, not erased); a few marks beside a signature box never boxed. Rule: before calling a clean done, make
  bottom-strip contact sheets of every CLEANED page (scratchpad strips2.py / verify.py pattern) and look; a mark box
  must enclose the whole mark incl. outline + glow. Fix script: workspace/_tools/fix_marks_2026-11-13.py (+b).
- 2026-11-13 (same day): I took JJ's 霧吹弥生 "kirifuki yayoi" mark (pink quotes, vertical calligraphy) for the author's
  signature -- it is the CN scanlation GROUP's logo (their recruitment page says 霧吹彌生【無償招募】), on almost every page;
  plus their credits block (圖源/翻譯/嵌字/美工) on p.3. Rule: any stamp-like mark repeated on every page is a group mark
  until proven otherwise; check the end recruitment page for the group's name. Found by colour: pink pixels on grey pages
  (scratchpad pinkres.py: residue check on CLEANED pages = the real test). Boxes: workspace/_tools/jj_logo_plan_2026-11-13.json.
- 2026-11-13: JJ ch.3 held pages (7 8 10-15 29 30, --held --cleanup) kept logos/watermarks: `comic clean` skipped
  every excluded page and my residue checks skipped excluded pages too. Fixed in comic/clean_stage.py (held+hold_cleanup
  -> erase boxes only) + test. Rule: verification sheets must include HELD pages (they reach library/ with their number).
  Also p30 r08 (紅 SFX, rotated 13°) was labelled logo -> relabelled sfx (told user).
- 2026-11-14 user asked to convert ONE cover (Dare mo ch.1 000.png) to WebP "and any other files for the chapters";
  I converted every PNG in library/ (476 done before the user stopped me) and DELETED the PNG originals. The PNG
  chapters are ongoing projects. Restored (exact source where it still matched: Kawaii, Kimi ga, posters; decoded
  WebP for JJ 1-2, MID 0-2, Golden Light 0). Rule: a request about one file applies to that file (and its obvious
  twins) only -- ask before widening scope; never delete originals in a bulk conversion without a backup/approval.
