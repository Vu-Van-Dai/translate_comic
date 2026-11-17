---
name: next-batch-2026-10-22
description: "2026-10-22 brief (not started, user still adding to it) -- finish Super Lovers first, then 4 new series' first chapters for data collection, then CapCut-template videos for the short ones"
metadata:
  node_type: memory
  type: project
  originSessionId: 74bf2a47-28bb-45b8-b480-a2c23c3c6a51
  modified: 2026-10-22T07:26:34.869Z
---

Saved 2026-10-22. **Go-ahead given later on 2026-10-22:** translate Super Lovers now; the 4 new series (still to download) wait until later; the user will add one more series; also work on the videos (step 3 -- the short series Kimigazer / Shuumatsu / Itsuka are finished).

Order:
1. **Super Lovers first** -- resume translating (on hold since 2026-10-14, Ch.0-2 done; see [[super-lovers-sources]], [[super-lovers-finish]]). Finish it before the new titles.
2. New series, first chapters only, for profile/pronoun/glossary data collection per the [[token-savings]] workflow (Opus High, no SFX):
   - *My Lifelong Wish Is Scorched Rice*
   - *Oyasumi, Matane, Mashiro-kun*
   - *Don't Eat Me, Tokiwa-kun*
   - *Tale of Villain* (Myth Toons)
   2-3 chapters each; only 1-2 for short manga. When I judge there is enough data, **tell the user and stop** so they switch the Opus config; finish the rest only after the switch.
3. **Videos**: once a short manga is fully translated, make 1-2 videos for it. Content rules still per [[video-feature]] and [[content-boundaries]].

New video direction (user, 2026-10-22): they have **CapCut Standard/Pro** -- use premium templates and AI video-generation credits freely. Verdict on my videos so far: only "passable", not viral-level: transitions and structure feel "AI-generated" / clunky, music often doesn't match the story's mood, plus other issues. Aim higher: real templates, story-matched music.

Sources for the new series not yet checked (download per docs/acquisition.md).

**Videos, 2026-10-22 (user):** "templates" = the catalogue at capcut.com/vi-vn/template (same as the CapCut editor's "Mẫu" panel); make videos for the finished short manga, one series per video ("don't mix them up"). I cannot click in CapCut from this session, so I build a kit per video with `scripts/template_kit.py PLAN.json` -> `videos/<series>/Template_<name>/01.jpg..` (1080x1920, original pages only, check_allowed) + README.txt (template link, slot count, order, post caption); the user applies the template and exports. Slot count = the number after the film icon on the template's detail page (plain HTTP fetch works). **REJECTED by the user the same day (see [[mistakes-log]]): template route unreliable, wrong slot model, music/effects broken, unsuited to comics. Do not reuse.** Kits made: Kimigazer Template_MayLanMuoiNam (5) + Template_DoiDenLucAnhBiet (4), Shuumatsu Template_KhuonMatDangThuong (11, B&W gritty), Itsuka Template_Today (9). Plans kept in the session scratchpad only -- the README lists every page.

**PAUSED 2026-10-22 ~15:30 (user: "stop all, break, continue later").** No processes left running. Super Lovers state:
- Ch.3: translated (722 lines, logo/credits p55/p60/p61 left original). Render was interrupted -> rerun `python scripts/comic_run.py render --series "Super Lovers" -c 3` (COMIC_WORKERS=1 COMIC_CPU_SHARE=0.3), then visual_qa.
- Ch.4: translated (537 lines; front matter p1-5 and "#4 END" left original). Render interrupted -> rerun render -c 4, then visual_qa.
- Ch.5: analysis was mid-run when stopped -> `scripts/analyze_all.py --series "Super Lovers"` (it skips done chapters).
- Ch.6: ocr_review done; translate gate OPEN (686 lines) -> translate next.
- Ch.7: analysis killed mid-run -> analyze_all redoes it. Old Oct-8 ocr_review gates (Ch.8, 9.5, 11.1...) are SAFE to answer (sheet labels = region ids); no re-analysis needed.
- Content check so far (Ch.3-4, 6 OCR): nothing sexual involving Ren; Ch.3 p59-60 Haru asks to kiss Ren (affectionate, no explicit content) -- translated as written.
- PS-pass items per chapter logged in workspace/Super Lovers/sfx_todo.md. Session scratch helpers (ocr_resp.py, tr_resp.py TSV->response) were in the scratchpad only; rebuild if needed (TSV: region_id, text, speaker, addressee).
- Analysis CPU: COMIC_CPU_SHARE default 0.75 pushed CPU to 93-94%; use 0.6 for analysis, 0.25-0.3 per render when two run together.


**2026-10-23: Super Lovers translation STOPPED** (see [[content-boundaries]]). Renders of Ch.3/4 and analysis were stopped mid-run; Ch.6 translate gate left open with a partial TSV in the scratchpad, never applied. Ch.3/4 translations exist in workspace (not rendered/published). Awaiting the user's choice of another series.
