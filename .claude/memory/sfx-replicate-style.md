---
name: sfx-replicate-style
description: "2026-11-09 SFX guideline -- replicate the original SFX ~70-80% (strokes, drop shadow, glow, vertical/slanted); vertical = one char per line + tabs for slant; split words = one text layer per piece"
metadata:
  node_type: memory
  type: feedback
  originSessionId: 7f16aa04-d4fc-4258-98e8-be067451a088
  modified: 2026-11-09T09:08:36.883Z
---

The user's updated SFX rule (2026-11-09, with editmanga's "[Type] Hướng dẫn type SFX cơ bản" as the reference):
- Replicate the original SFX's LOOK by about 70-80%: stroke(s) (often two: inner + outer), drop shadow, glow,
  and its orientation -- vertical or slanted.
- Vertical / slanted text: ONE CHARACTER PER LINE, tabs at the start of lines to create the slant (staircase), then
  a slight rotation of the layer if needed (reference: "RETT" stacked R/E/T/T, rotated ~2°).
- Font need not match; pick a similar face or keep the series' SFX faces. Overall appearance > exact font
  (styles change anyway going from EN/JA/KO/ZH to Vietnamese).
- An SFX drawn in 2, 3 or 4 separate pieces -> one text layer per piece (e.g. "QUẪY" as "QU" + "ẪY" placed where the
  original's pieces are).

- Degree (user, same day): 70-80% is the target for MANHUA and MANHWA; for MANGA the SFX may change much more.
  Even in manhua/manhwa 50-60% -- or an entirely different SFX -- is fine when the original is very distinctive;
  a close approximation is enough. So: reproduce what is clearly there, never force it.

**Why:** the user's typesetting standard for the group. Supersedes "near-vertical SFX: erase with LaMa, set by hand"
and "50-70% of difficult SFX is enough" in [[throughput-over-perfect-sfx]].
**How to apply:** comic/photoshop/sfx.py + driver.py addSfx (PSD build). See [[typer-tools-styles]].

**Built 2026-11-09 (untested on a real PSD -- user: test later, more SFX methods coming from editmanga videos):**
comic/photoshop/sfx_style.py (effects: 2nd stroke via outer glow 100% spread, soft glow, drop shadow, gradient
overlay; textured/patterned fill -> build note "finish by hand"; pieces + split_text -> one layer per piece;
vertical_text + slant_of -> one letter per line + tabs), wired in sfx.sfx_layers + driver.py JSX (sfxFx,
verticalLines). tests/test_sfx_style.py. Dry-run script: scratchpad sfx_try.py (NH ch.8, p026 vertical TÉ).
