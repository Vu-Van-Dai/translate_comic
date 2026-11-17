---
name: sfx-erase-strokes
description: Erase ALL text (dialogue, SFX, logos/watermarks) stroke by stroke -- mask traces each stroke, slightly wider so outline/glow goes; whole-box erase only as fallback. Test later with the SFX work.
metadata:
  type: feedback
---

User (2026-11-10, screenshot of a web typesetting tool erasing a pink SFX with white outline, then a follow-up): when erasing, target only the text itself -- trace over **each stroke** of each character, the mask a little **wider than the stroke** so its rounded white outline and any surrounding effect (glow, shadow) is removed completely. "This deletion method removes only the specific text, preventing the erasure from spreading to the surrounding areas -- which seems to be happening currently. I suspect you might be selecting an entire region... you can still delete an entire region if selecting individual pieces of text isn't possible."

**Why:** erasing a whole box/region spreads into the artwork around and between the letters (then LaMa repaints it badly); a mask exactly on the ink leaves the outline as a ghost rim.

**How to apply (all text, not only SFX):**
- Default mask = per-stroke: the lettering's ink *including its outline/halo*, dilated a few px past the outer edge; everything between and around strokes stays original.
- Whole-box/region erase only as the fallback when strokes cannot be separated (e.g. faint watermark, text fused with art) -- and say which regions used the fallback.
- Places in the code that erase more than strokes today (found 2026-11-10, to fix at test time): `mask.with_erase_boxes` (logo/watermark/credit: whole bbox = 255); SFX `letters: area` and `colour` modes in `comic/photoshop/sfx.py`; the comic-text-detector mask itself is often a blob, not strokes (dialogue: `GLYPH_DILATION = 3` on that blob).
- Check every result for leftover outline rims and for erased art outside the strokes.
- **Testing deferred**: test later together with the SFX replication work ([[sfx-replicate-style]]). Don't start until the user says.
- Related: [[clean-screentone]] (what to fill the erased area with), [[mistakes-log]].

**2026-11-10 follow-up (user, screenshots of Douluo Dalu II ch.598 with 嗒! and 摇晃—— painted over in red brush strokes that hug the letters):** "The current area is a rectangle, right? ... a rectangle might sometimes extend outside the intended bounds -- make the area follow the text instead." Plan I proposed (not yet confirmed by the user; build at SFX test time):
1. Automatic: per region, compute the stroke mask (ink + outline, dilated) and use it instead of the box.
2. Studio: show that mask as a red overlay per region, plus a **brush / eraser tool** to paint the erase area by hand (like the red strokes), stored per region (e.g. `studio/<page>_<region>_erase.png`); the cleaner prefers the painted mask > auto stroke mask > box.

**User clarification (2026-11-10):** stroke-following masks matter for the SFX that need real redrawing and that Photoshop cannot handle (those go to LaMa anyway); small leftovers the new text covers are fine. SFX test (style + erase) started the same day on NH ch.8.
**Built + tested 2026-11-10 (NH ch.8, 16 SFX on 10 pages):** `sfx.keep_strokes` (colour-mode SFX only): pieces found by colour are kept whole when the detector blob covers >= STROKE_SHARE 0.08 of them (min 20 px); long straight runs (>= LINE_SHARE 0.8 of the lettering's width/height, min 70 px: panel borders) cut out first; pieces fused with art keep only their detector-marked part; outline gaps closed (5x5) and enclosed fill (third-colour letter fill, THUD grey) taken. `sfx_style.split_text`: no split when pieces are very unequal (SPLIT_MIN_SHARE 0.35; LÓE was "LÓ"+"E"). Test in tests/test_sfx_style.py. Results: hair/hand/shelf dots no longer erased; p6 BỊCH smudge gone; LaMa beat Content-Aware on p4 PHÙ. Open: p31 きゃっ over panel border + dot tone -- LaMa leaves a dark ghost, Content-Aware leaves pale rims on the dot tone -> needs the dot-pattern fill ([[clean-screentone]]). sfx_response.json in NH ch.8: lama for p004_r06, p006_r02 (+ colour [124,124,124] -- user: BỊCH colouring was off, grey like the original THUD); p031_r09 きゃっ = NOTE "kya kya: khúc khích" (user: tricky, a note is fine), left as drawn. Backup of pre-test PSDs/PNGs: %TEMP%/nh8_ps_backup. Studio brush tool: not built yet.
