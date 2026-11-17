---
name: text-follows-bubble-contour
description: 2026-11-15 rule -- dialogue text blocks are stacked to follow the bubble's contour (oval/diamond stacks), built into fit._contour_wraps
metadata:
  type: feedback
---

When typesetting dialogue, the text block must take the speech bubble's shape: short lines where the balloon
narrows, the longest lines in the middle, a squarish block in a square box (the user, 2026-11-15: "shape the
text blocks to follow the contours of the speech bubbles -- much more visually appealing").

**Why:** a single-width wrap gave rectangles with a stub line under them; the user finds contour-shaped
stacks look much better.

**How to apply:** built into `comic/typesetting/fit.py` (`_contour_wraps` + `_follows` tiebreak; docs/lettering.md
section 5), so every `comic ps build` / render after 2026-11-15 does it automatically. PSDs built before that keep
their old line breaks unless rebuilt -- ask before rebuilding anything. Meaning still beats shape: when I write a
line, bind words that must stay together with a no-break space. Related: [[dialogue-fonts]], [[throughput-over-perfect-sfx]].
