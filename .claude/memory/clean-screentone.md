---
name: clean-screentone
description: Cleaning text over dot screentone must restore the dot pattern exactly; user says I often get this wrong
metadata:
  node_type: memory
  type: feedback
  originSessionId: 7f16aa04-d4fc-4258-98e8-be067451a088
  modified: 2026-11-10T01:46:14.363Z
---

When cleaning lettering that sits on dot screentone / patterned backgrounds (e.g. captions and floating text on a dotted panel), the erased area must be refilled with the SAME pattern (dots aligned to the surrounding grid), not a smudge, flat grey or blurred LaMa patch. User (2026-11-10, Studio screenshot of a manga page with text on dot tone): "be careful when cleaning this part; there's a pattern, so make sure to fill it correctly... I've noticed you tend to make this error quite often."

**Why:** a smeared or misaligned patch on regular dot tone is immediately visible and spoils the page/chapter.

**How to apply:**
- Current code (`comic/image_processing/clean.py`) only routes non-plain backgrounds to LaMa, which tends to blur regular dot tone. For regular/periodic tone prefer a pattern-aware fill: estimate the dot period (autocorrelation of the surrounding band) and fill masked pixels from the same pattern shifted by whole periods; fall back to LaMa / Photoshop Content-Aware Fill (`--clean hybrid`) only for irregular texture.
- Before building PSDs, visually check every cleaned region that sat on tone/pattern (crop before/after) and fix bad ones; never hand over a chapter with smeared tone.
- Related: [[hitl-workflow-2026-11-08]], [[colab-only-lama]], [[mistakes-log]].
