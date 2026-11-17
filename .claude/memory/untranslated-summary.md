---
name: untranslated-summary
description: "2026-11-01 user request -- after all remaining series are translated, give a TABLE of chapters/pages left untranslated; untranslated pages STILL get logo/watermark removal (erase_marks / --cleanup)"
metadata:
  node_type: memory
  type: feedback
  originSessionId: 09b1bc9c-97e4-4339-bd3d-2160c5cfede8
  modified: 2026-10-31T17:11:41.128Z
---

User (2026-11-01): once every remaining series is translated, compile a summary **as a table** of the total number of chapters and pages left untranslated (per series, with totals and reason).

Logos/watermarks: untranslated pages **do** get logo and site-watermark removal through the existing mechanism (`scripts/erase_marks.py`, `comic exclude --held --cleanup`). I first misread the request as "keep the logos" and the user corrected it the same day: "Why don't you remove the logos and watermarks? We've already set up a dedicated mechanism for that." Do not revert cleanups already done.

**Why:** the user wants one overview of what was skipped, and wants every page in library/ free of other groups' logos/watermarks.
**How to apply:** at the end of the batch ([[priority-batch-2026-10-31]]), build the table from `workspace/<series>/sfx_todo.md` skip logs, `comic holds --series S`, and the chapters/pages not processed per [[content-boundaries]]. Columns: series | untranslated chapters | untranslated pages | reason. Run erase_marks on untranslated/held pages as usual ([[watermarks-erase]]).

**ORDER CHANGED (user, 2026-11-11):** logo/watermark removal on untranslated/held/kept-as-is pages happens BEFORE the
PS review, not at publish. Marks must already be gone in workspace/<S>/<Ch>/cleaned/ (and so in the PSDs) when the
chapter is handed over -- otherwise a hard-to-remove mark forces a second review. How: before a chapter's PSD build,
run erase_marks' site_lines() template scan over the cleaned pages + check for undetected group/site credits; label
missed ones `erase` (boxedit +erase:...) and re-clean those pages. Hard marks: fix them myself before handing over,
-- send hard marks to LaMa on the Colab T4 to redraw (user 2026-11-11: colab_clean.sh with the mark labelled `erase`, COMIC_DRIVE="H:/My Drive"); list one in the handover only if LaMa also fails.

**SCOPE (user correction 2026-11-11):** "untranslated pages" here = the pages/passages I SKIPPED (content skips,
hold_reason, content_skips.log) in chapters whose boxes are reviewed. Chapters/pages whose OCR boxes the user has NOT
reviewed yet (e.g. ASG 6+) are not touched at all -- no mark scan, no exclude, nothing -- unless it is an old-workflow
project where I verify the boxes myself. Check with comic.check.unreviewed_pages(paths, state, None).

**FINAL FRAMING (user 2026-11-11, supersedes the "separate scan" wording above):** logo/watermark removal is NOT a
standalone task -- it is part of CLEANING. In every chapter's clean phase (dialogue, SFX, Photoshop fill or LaMa) the
marks are erased in the same pass, same as untranslated/skipped lines are still cleaned. So: marks must be labelled
`erase` by the time the chapter is cleaned (box review), clean erases them with everything else, hard ones go to LaMa
on Colab in that same clean. The PSDs the user reviews are therefore already mark-free. A mark scan is only a check of
my own cleaning, never a separate step.
