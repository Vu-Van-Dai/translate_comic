---
name: watermarks-erase
description: Other groups' credit tags and site watermarks inside story pages are erased completely by default
metadata:
  type: feedback
---

Any credit tag or watermark from another group/site that sits inside story content gets removed. Default is to erase it completely (`erase_only: true` in the Photoshop route's sfx_response). Replacing it with "Raichi" (our watermark) or "Rồng nhỏ" (our group) is allowed but second choice — the user said "it's best to just remove it completely".

Examples the user showed (2026-10-11):
- Super Lovers page 002: small scanlator tag at the bottom edge, "SWEET PURINSU SCANS | TUMBLR".
- A webtoon page from truyentranhdammyy.site: semi-transparent block over the art, "WEB CHÍNH CHỦ: MEOMEOTEAM.SITE HOẶC TRUYENTRANHDAMMYY.SITE" plus two lines of small print above it.

**Why:** the user's standing rule; matches `docs/photoshop.md` (moved there from CLAUDE.md 2026-10-20) "Other groups' watermarks and credit tags on story pages are erased".
**How to apply:** detection misses most of these (small, faint, over art), so OCR/scan the whole page for group names and site URLs before building, and add a `bbox` + `letters: colour` item when the detector never marked it. Whole credit pages are still the user's call.

**Whole credit pages DECIDED (user 2026-11-11):** another group's credits / recruitment page (staff names, twitter/tumblr
links, "we need cleaners") is NOT published at all -- `comic exclude -p P --reason ...` (no --held), nothing to clean.
Done for ASG ch.1-5 and KS ch.1-6 (box-reviewed only). ASG 6-21 restored: their boxes are not reviewed yet (ASG 20: credits p34, p35 is story art). Find them by
grepping analysis for tumblr|twitter.com|translation:|typesett|proofread|cleaning:|e-mail; check the hit is not a story page.
Mark scan note: erase_marks site_lines() templates are the Chinese mw-site lines; on these series every hit >=0.3 was a
panel border (false positive), 2026-11-11 scan of 99 chapters found no real site watermark.
