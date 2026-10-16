---
name: super-lovers-sources
description: Super Lovers re-sourced 2026-10-11 — which comix uploads are full chapters, what was archived, what is still missing; translation on hold
metadata:
  type: project
---

On 2026-10-11 the user found Super Lovers chapters were the short "pocket" versions. Cause: on comix.to (title 63m0z-super-lovers, "All groups" list, 6 pages) Ch.1–10 exist both as Sweet Puriinsu "Booklet N" uploads (DVD booklets, 10–18 pp) and as the real volume chapters (September Scanlations / Bliss Scans, 49–62 pp). The earlier import took the booklets.

Replaced with full chapters (all verified by title/END pages): 1,2,3,4,5,7,8,9,10 (September Scanlations), 12 (September), 15 (Hoshikuzuu), 17, 18 (Sweet Puriinsu), 46 (Tsuini Koibito; old copy had 3 pages). Imported via raw/ + `comic import-raw --language en --kind paged --direction rtl`. Old versions, incl. the finished booklet translations of Ch.1–5 from library/, moved to `workspace/_archive/Super Lovers/replaced_2026-10-11/` (not deleted).

Still missing: **Ch.41** (comix jumps 40 → 41.6). Extras: only Extra 2 is in (as Chapter_000); comix also has Extra 1 (Vol.7), Extra 3 (Vol.9). User's fallback source: myreadingmanga.info/artist/abe-miyuki/ (vols 1–16) — Cloudflare + 18+ gate, the user must clear it themselves in `comic chrome --edge --port 9223` (user prefers Edge).

comix reader lazy-loads, so `comic download` gets ~2 pages; harvest by scrolling via CDP and capturing image responses, then import-raw.

Ch.15/17 (Hoshikuzuu/Chinese-raw scans) carry Chinese group watermarks on the art — erase at build ([[watermarks-erase]]).

Progress: Ch.0, Ch.1 and Ch.2 published (Photoshop route, WebP) by 2026-10-13. The user reviews each chapter and fixes small leftovers in Ch.1 by hand; continue chapter by chapter when they say so.
