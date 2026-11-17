---
name: current-work
description: What the user has me working on now (2026-10-14) -- Super Lovers on hold, Dasadanan + Destined to Meet on the Trends via analyse-everything-first
metadata:
  type: project
---

2026-10-14 the user put **Super Lovers on hold** (Ch.3 not started) and switched translation to **Dasadanan** (35 chapters incl. 17.5, 27.5, 31.5 epilogue; Ch.32 never downloaded, Ch.33 download failed) and **Destined to Meet on the Trends** (85 chapters; Ch.78 missing). Both are colour webtoons, English scanlations from yaoiscan (`--language en`, longstrip).

Workflow the user asked for: **run analysis (slice + detect + OCR) on every chapter first**, then go back and review/translate chapter by chapter. `scripts/analyze_all.py --series ...` does the batch (one log per run in `workspace/_logs/`), skipping chapters already at a gate.

Destined had duplicate "Chapter_00N.1/.2" folders -- partial 1200 px re-uploads of chapters that already exist whole at 700 px. Moved them to `workspace/_archive/Destined to Meet on the Trends (1200px part re-uploads)/`. Kept 3.1 + 3.2 (the only copy of Ch.3) and 16.5 (a real side chapter).

**How to apply:** finish the batch, then ocr_review -> translate -> render -> Photoshop route per chapter, Dasadanan first unless the user says otherwise. Same finishing standard as [[super-lovers-finish]]; read [[mistakes-log]] first.

**Paused 2026-10-14 (user: "take a break, continue later").** To resume:
1. Channel videos: DONE 2026-10-14 -- all 147 folders have `edit - <song> - <artist>.mp4` (138 distinct tracks, 10 styles). Re-running the script only picks up new folders.
2. OCR batch: `python scripts/analyze_all.py --series "Dasadanan" --series "Destined to Meet on the Trends"` -- Dasadanan all done, Destined done up to Ch.17 (Ch.18 was interrupted, will redo).
3. Then translation chapter by chapter, Dasadanan first. Mi Imitador's 15 videos are published; the user hasn't answered whether to erase the Bato.to/MANGAGO banners in its library pages.

**2026-10-14 later: user reprioritised -- translate the SHORT manga series (5-12 chapters) first**, before Dasadanan/Destined: Kiss Shite Sugar-kun (6), Kimigazer (6), Itsuka, Kazoku ni Naru made (6), Shuumatsu, Kimi to (11). All English scanlations, B&W, paged rtl; analysed with scripts/analyze_all.py first. **Kiss Shite Sugar-kun: SKIPPED** (2026-10-14) -- sexual romance between high-school students throughout (nude locker-room scene ch.1 p.16-18, sexual talk/scenes ch.4); per [[content-boundaries]] not translated; told the user. Others: adults/family. Dasadanan Ch.1 is parked at its translate gate (profile made: fonts.yaml preset manhwa). Destined analysed through Ch.19.

**Paused 2026-10-15 ~10:30 (user: "stop all, continue later").** State:
- Fixes done this session: bubble-centred placement (`fit._bubble_centre`, `fit._centred`), even baseline grid (`render.line_tops`, PS plan follows), Felt-TB for manga captions (Super Lovers pinned to Augie), `scripts/centre_check.py`. Tests 360 pass.
- Kimigazer: Ch.1, Ch.2 rendered with fixes (visual_qa gate open; remaining off-centre: Ch.1 11, Ch.2 9 -- check by eye, several are SFX "Ẳng/Gâu" or shared/leaky areas). Ch.3 render was interrupted -> re-run `comic render -c 3`. Ch.4, Ch.5 translate responses written (handoff/translate_response.json) -> `comic resume`. Ch.6 needs analysis.
- Shuumatsu, Kimi to: Ch.1-4 translate responses written -> `comic resume` each. Ch.5-11 need analysis (`scripts/analyze_all.py`, COMIC_WORKERS=1).
- Itsuka: Ch.4-6 at ocr_review; Ch.1-3 need analysis.
- Hard SFX / merged items noted in `workspace/<series>/sfx_todo.md`. Scratch translation files (kg4/kg5/sk1-4 *_vi.tsv) were in the session scratchpad -- the responses in handoff/ are the durable copy.

**2026-10-15 ~14:05 progress:** Published (pipeline render, PNG) after eye-checked visual_qa: Kimigazer Ch.1-5, Shuumatsu Ch.1-4. Still to do for those: Photoshop route (SFX pass per [[throughput-over-perfect-sfx]], items in `workspace/<series>/sfx_todo.md`, notes via `kind: note`), then `comic ps build` + leftover_scan + `ps publish` (WebP). Analysis queue (`workspace/_logs/queue4.sh` -> analyze_all) running: Kimigazer Ch.6, Shuumatsu Ch.5-11, Itsuka Ch.1-3. Next: Kimigazer Ch.6 ocr_review -> translate.

**2026-10-15 ~15:00 (user switched to channel videos).** Translation state to resume:
- Kimigazer: Ch.1-5 published (pipeline PNG). Ch.1 PS build DONE (photoshop/, 32 sfx items incl. notes) -> next `comic ps publish` after leftover_scan. Ch.6 translate_response written -> `comic resume` (render), then QA. Ch.2-6 SFX passes + PS builds pending.
- Shuumatsu: Ch.1-4 published (pipeline). Ch.5 translate_response written -> resume. Ch.6 ocr_review_response written (sk6.txt) -> resume, then translate. Ch.7-11 at ocr_review.
- Itsuka: Ch.1, 4-6 at ocr_review; Ch.2-3 analysis was killed (exit 1, session end) -> re-run analyze_all for Itsuka.
- 2026-10-15 night: user paused translation until 16/10 ("handle the story translation tomorrow"). Also due 16/10 after ~23:10: YouTube remaining 52 uploads (max 50/day).
- 2026-10-16: Kimigazer Ch.1-6 ALL published (pipeline PNG; Ch.1 PS build done). Shuumatsu Ch.1-11 ALL translated; Ch.1-4 published, Ch.5-11 rendering (queue7/queue8 in workspace/_logs) -> visual QA each. Next: Itsuka Ch.1-6 (all analysed, at ocr_review). Final pass later: SFX + PS build + ps publish per chapter, items in workspace/<series>/sfx_todo.md.
- 2026-10-16 later: **Shuumatsu Ch.1-11 ALL published** (pipeline). Kimigazer Ch.1-6 all published. Itsuka Ch.1 translated + rendering; Ch.2-6 at ocr_review. Remaining final pass (both finished series): SFX + PS build + ps publish (WebP), items in sfx_todo.md.
- 2026-10-21: **Itsuka Ch.1-6 ALL published** (pipeline; Ch.6 = Christmas special p.3-5 + thanks p.12, gallery/ads/colophon left original). All three short series (Kimigazer, Shuumatsu, Itsuka) are now fully translated. Left: final pass for all of them -- SFX + PS build + leftover_scan + ps publish (WebP), items in workspace/<series>/sfx_todo.md -- per [[token-savings]] workflow at Opus Medium.
- 2026-10-21: user: **Dasadanan paused** -- they will redraw/remove its dialogue and SFX by hand (or do dialogue themselves and let me add SFX later). Its Ch.1 translation draft (my TSV) was not applied. Priority now: finish the SFX/Photoshop pass for Kimigazer, Shuumatsu, Itsuka.
- 2026-10-21 SFX/Photoshop final pass in progress. Published WebP: Kimigazer Ch.1-2. sfx_response.json written for every chapter of Kimigazer (1-6), Shuumatsu (1-11), Itsuka (1-6). Builds run via `workspace/_logs/ps_queue.sh` (log ps_queue.log). Per chapter after build: sfxqa sheet check (skipped "near-vertically" -> give `angle` or make it a note), `scripts/leftover_scan.py <photoshop dir>`, then `comic ps publish`. Kimigazer Ch.3 needs rebuild p.4,19 (watermark + GÀOOO added); Ch.4 p.13-18 extras added.
- 2026-10-21 PAUSED (user: stop all). Published WebP: Kimigazer Ch.1-3. Kimigazer Ch.6 build interrupted at 36/37 PSDs -> rerun `comic ps build --series Kimigazer -c 6` (or -p for the missing page). Then run `bash workspace/_logs/ps_queue.sh` after removing its wait loop (Kimigazer 4/5 fixed pages, Shuumatsu 1-11, Itsuka 1-6). Then per chapter: sfxqa check, leftover_scan, ps publish. Kimigazer Ch.5: re-check p.7 BỤP sizes + p.13 fare-card line after rebuild.
- 2026-10-21 17:10 PAUSED. Published WebP: Kimigazer Ch.1-5. Queue ps_queue.sh built Kimigazer 4 fix, Shuumatsu 1-11, Itsuka 1 (+2 if finished; see ps_queue.log). Remaining: build Itsuka 3-6 (Ch.3 was interrupted at 17:05), then every line in workspace/_logs/ps_after.txt (fix rebuilds, use the same run() function), then per chapter sfxqa check + leftover_scan + comic ps publish. Shuumatsu 'SFX: xxx' label captions now use plain label style (keep_case/outline false/size_by box).
- 2026-10-22: queue ps_queue2.sh (via scripts/comic_run.py) builds Itsuka 5-6 then fix rebuilds. Next: run workspace/_logs/ps_after3.txt lines (watermark strips: whole black strip bbox + lama + halo 6, ink light). Then sfxqa + leftover_scan + publish all remaining chapters.
- 2026-10-22 ~10:00: **SFX/Photoshop final pass DONE** -- Kimigazer 1-6, Shuumatsu 1-11, Itsuka 1-6 all published to library/ as WebP (ps publish). Known small leftovers: Kimigazer Ch.3 p.4/p.35 faint watermark trace; Kimigazer 6 p.40 'Afterlife Eats?!' (store name) kept; Shuumatsu 6 p.14 bubble top left empty after erasing 'I'M SORRY,'. Next per user: Dasadanan is the user's (manual redraw); otherwise await instructions.
- 2026-10-22: Kimigazer rainbow+eggplant watermarks (16, ch.1-4): 10 erased by LaMa, 6 covered with Rồng Nhỏ logo (photoshop/logo_stamps.json + scripts/stamp_logo.py; re-stamp after any rebuild of those pages: ch1 p12/22/32, ch2 p2/31, ch3 p11). Republished ch.1-4. Smart App Control off -> comic.exe works again.
