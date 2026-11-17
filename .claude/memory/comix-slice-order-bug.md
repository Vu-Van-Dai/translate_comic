---
name: comix-slice-order-bug
description: comix downloads (MID, Walking in the Rain, Dare mo) saved long-strip slices out of order -> bubbles cut at seams; how it was found, tools, state 2026-11-08
metadata:
  type: project
---

**Bug:** `workspace/_tools/comix_fetch.py` numbers slices in the order it first SEES them while scrolling
(`seen.setdefault(src, len(seen))` over `document.images` filtered by `i.complete`) -- a lazily loaded later
image can be seen before an earlier one, so slices get swapped. Nondeterministic: a fresh download can be
wrong too, so a re-download is NOT a trustworthy reference. Fix the fetcher to order by DOM/scroll position
before any re-download.

**Detector (trustworthy, no download):** `scripts/seam_check.py "<series>"` -- edge mismatch at each slice
seam vs. the slices' own row-to-row variation. Known ch.30 seam: correct 2-10, swapped 70-120. Report for
all MID: `workspace/_logs/mid_seam_check_2026-11-08.txt` -- 706 flagged seams; NOT yet separated from real
panel breaks that fall on a seam (white gutter -> panel top also jumps). "swap fixes" = strong evidence.
**Fixer:** `scripts/fix_slice_order.py <series> <ch> <fresh_dir> [--check-only]` reorders originals to a
reference order, re-cuts, keeps pixel-identical pages' analysis/Studio edits/reviewed (backup in
`_backup_before_reorder`). Already applied (from fresh downloads, so re-verify with seam_check):
ch.30 (057/058 swap; `_backup_before_slice_swap`), ch.29 (23 slices; 53 pages re-detected), ch.28.
Affected confirmed by the user: MID ch.8, 10, 26, 29, 30 (incl. translated/published ch.0-27).

**Plan agreed with user:** fix every affected chapter; translated ones keep unchanged pages' Vietnamese;
re-cut pages get re-detected and old translations re-matched by English source text; republish ch.0-10.
Also check Walking in the Rain and Dare mo (same downloader). User asked to finish NH ch.8 first.
**Why:** the user saw cut bubbles in Studio (2026-11-08).
