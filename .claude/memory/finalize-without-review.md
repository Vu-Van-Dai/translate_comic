---
name: finalize-without-review
description: Finish ALL cleaning (incl. LaMa on the T4) and build complete PSDs BEFORE showing the user; one review in Photoshop, then publish WebP on approval
metadata:
  type: feedback
---

Order per chapter (the user, 2026-11-08, correcting my first reading):
1. I do everything first: `comic clean` (flat fills + LaMa on the T4 for text over art/characters/hair),
   SFX handling (Photoshop Content-Aware Fill; LaMa for SFX Photoshop cannot set, e.g. near-vertical),
   `comic ps build` -- complete PSDs.
2. Then open all the PSDs in Photoshop for the user's single review; say which pages carry flagged spots
   (skipped SFX to set by hand, leftover ink).
3. Only after the user approves: `comic ps publish` -> WebP in `library/`.

**Why:** showing PSDs before LaMa was done meant the user reviewed, then I supplied LaMa results and
re-rendered pages that had needed no change -- a wasted round.
**How to apply:** never open PSDs for review while cleaning/LaMa is still pending; never rebuild a page
the user saved (ps build skips them unless --overwrite-edited). See [[hitl-workflow-2026-11-08]].
