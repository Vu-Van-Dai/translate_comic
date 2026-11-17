---
name: credits-erase-rule
description: "Group credits/logos/watermarks/site links on story pages are ERASED (Studio 7/8/9 = logo/watermark/credit), with LaMa never flat; bilibili + end-of-chapter credit/warning pages stay as drawn"
metadata:
  node_type: memory
  type: feedback
  originSessionId: e70ab6c1-06d7-4fad-88b0-f3a3592f1526
  modified: 2026-11-12T12:40:12.487Z
---

User 2026-11-12 (several corrections in one evening):
- Erase every watermark, credit, logo and inserted site link of CN/EN/KR/JP scanlation groups and aggregator sites on
  story pages -- "they are not the ones who made the Vietnamese version". I had wrongly treated group credits/logos/notes
  as "keep / stays in English" in my own reviews (old CLAUDE.md "front matter" wording) -- that is the thing to find.
- KEEP: bilibili logo + "BILIBILI COMICS EXCLUSIVE" notice; credit or warning PAGES at the very end of a chapter
  (published as drawn, NOT excluded -- e.g. KS ch.6 p17, JJ ch.5 p43); series title logo; in-world signage.
- In Studio the group marks are always labels 7/8/9 (logo/watermark/credit). Boxes the user drew are correct -- never
  delete or relabel them.
- Cleaning marks: NEVER flat fill ("loses story detail") -> always LaMa (Colab). Code now: whole box +3 px, plus the
  mark's coloured glow on grey pages, mirrored context at page edges, grey guard per page (no blue smear).
  Stroke-tracing inside the box was tried and dropped (ghosts). User wants "better models" explored for content loss.
- Don't hunt marks with OCR filters ("OCR boxes are circled incorrectly") -- check boxes yourself visually.

**Why:** two code bugs hid the user's boxes: empty-text gate skipped every Studio-drawn mark (358 pages), and
mark-only pages got no PSD so publish copied the original. Both fixed 2026-11-12 (TextRegion.cleanable; plan/publish).
**How to apply:** box review -> label group marks 7/8/9, never delete/keep them; re-clean with COMIC_DRIVE set.
Related: [[watermarks-erase]], [[resume-2026-11-12b]].

**End-of-chapter group recruitment / site-ad pages (user 2026-11-13):** KEEP them in the chapter as drawn (no exclude,
no erasing) -- the user deletes them by hand after the library export, "so you don't remove something you shouldn't".
Applies to JJ/Kawaii/KKLNY ends (霧吹彌生, 绿茶, 萌木 recruitment; mw777/manwa ad pages) and any like them.
