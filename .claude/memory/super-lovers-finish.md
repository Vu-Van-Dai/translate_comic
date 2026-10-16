---
name: super-lovers-finish
description: A Super Lovers chapter is finished only after the Photoshop route (sfx review, build, publish); no voices — one face per kind, like Ch.0
metadata:
  type: feedback
---

The finished product for Super Lovers is the **Photoshop route output**, not the pipeline render. Per chapter: pipeline (ocr_review → translate → render → visual_qa) → `comic ps sfx` (translate meaningful English SFX, erase other groups' tags, translate/erase undetected asides with bbox + letters:colour) → `comic ps build` → check the PNG previews → `comic ps publish`. Never leave the pipeline render in library/ as if done.

Erasure is split by what is being erased (user, 2026-10-12): **dialogue/captions** -> the pipeline's own cleaning (flat fill on plain bubbles, LaMa over texture), i.e. `comic ps build --clean pipeline`; **SFX** -> Photoshop Content-Aware Fill (the SfxItem default), with `"fill": "lama"` only for an SFX where CAF copies something in (as Mi Imitador did: 0-4 per chapter). Never `--clean content-aware`: on ch.1 p.4/6/7/8 it left scratch marks in white bubbles and redrew a bubble outline through the text, where the pipeline was clean. The user: "LaMa cannot match Photoshop for SFX". After the build run `python scripts/leftover_scan.py workspace/<series>/<ch>/photoshop` and look at every flagged spot by eye: it found the tiny "SFX: …" labels at page edges, untranslated asides ("Good grief", "Yeah yeah", "That's my Japanese side showing", "Kinda freaking me out…") and unreplaced SFX (PFFT, FWAP) the review sheets missed. Fix with bbox + letters:colour items (kind caption/dialogue for text, keep_case+Mali for small handwritten SFX), rebuild those pages, re-crop to confirm, then publish.

**Do not use `voice` at the translate gate for this series.** Ch.0 used none; Ch.1 used angry/menacing/cheerful/soft and the user flagged the result (2026-10-12): a scratchy face over dot tone, several faces on one page, a burst-bubble line spilling over its spikes (p.44), English SFX left (SPLASH, head shake, p.46).

**Why:** user compared Ch.1 against Ch.0 and called out "a lot of errors". **How to apply:** match Ch.0's look; review rendered pages by eye for text crossing a bubble edge, not just the pre-check list. See [[watermarks-erase]], [[super-lovers-sources]].

Checks added 2026-10-13 after the user's ch.1 review: `scripts/spill_check.py --series S -c N` lists lines whose ink leaves their bubble (false positives on dot-tone captions — look at each). Joined bubbles that the detector made one region (two speech balloons touching) must be split with two ps items (kind dialogue, letters mask, `replaces`). Scanlator "SFX: …" labels are SFX items (CAF erase, AnimeAce font, ink dark, outline, size_by box), never kind=caption (that path smeared a grey box). Augie font advance bug fixed (see CLAUDE.md); Ch.0 was built with the broken widths.
