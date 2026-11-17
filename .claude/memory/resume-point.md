---
name: resume-point
description: "Exact place to pick up when the user types \"continue\" (saved 2026-10-23) -- Super Lovers Ch.3-6 state, Ch.4 QA loop to debug, then Destined to Meet on the Trends"
metadata:
  node_type: memory
  type: project
  originSessionId: d8319da0-3e33-408c-a4f7-abebca06ae45
  modified: 2026-10-23T14:10:01.900Z
---

**Superseded 2026-10-26: the user put this queue aside -- work from [[priority-batch-2026-10-26]] first.** Saved 2026-10-23 at the user's request so a new session can resume on "continue". Read [[content-boundaries]] and [[mistakes-log]] first. Order of work: finish Super Lovers Ch.3, 4, 5, 6 -> then *Destined to Meet on the Trends* (adult celebrity fake-dating BL, OK to translate; Ch.1 at ocr_review, 43 items; no profile yet -- create one, preset manhwa, colour webtoon `--language en`).

**User rule (2026-10-23): whenever I decline to translate anything, give the reason** (page, region, why) in the reply and in workspace/<series>/sfx_todo.md. Content rules for minors are still being refined with the user -- ask/explain, don't silently drop.

Super Lovers state (nothing running; no python processes left):
- **Ch.3**: translated (722 lines) but NOT rendered (render killed twice). Next: `python scripts/comic_run.py render --series "Super Lovers" -c 3` (COMIC_WORKERS=1 COMIC_CPU_SHARE=0.5), then visual_qa. Its romantic Haru/Ren lines are translated -- fine under the current rules.
- **Ch.4**: at visual_qa. **BUG to investigate first**: answering passed=true (even with `accept` for every error pre-check) re-opens the gate showing the *other* page set -- it alternates between pages [10,12,14,15,22,42,48] and the other 36 pages, round stays 1, never publishes. Read comic/qa/visual.py (`record_approvals`, `pending_pages`) and comic/cli/app.py `_resume_visual_qa` before answering again. All pages were already eye-checked; remaining residue = small asides/SFX for the Photoshop route. translation.json had " / " joins removed by hand.
- **Ch.5**: at ocr_review (analysis done). Contains drunk Haru demanding a kiss from Ren and "going further" -- review page by page with content-first judging; skip only sexual lines, give reasons.
- **Ch.6**: translated (644 lines), rendered, now at visual_qa (50 pages shown). Pages 44-48 (drunk Haru forcing a kiss on Ren, 13) were left out at translate; draft of their dialogue is in workspace/Super Lovers/Chapter_006/handoff/p044-048_translation_draft.tsv (keeps p044_r03 "kiss", p046_r01 "NGH...", p046_r02 "HA...!!" in English -- reason: a minor's gasps during a coercive kiss). To add them: after QA, re-open translate for those pages (`comic translate -c 6 -p 44 ... -p 48`), answer from the draft, resume, render.
- Ch.7+: analysis was stopped mid-run; `scripts/analyze_all.py --series "Super Lovers"` (COMIC_CPU_SHARE 0.4-0.6) resumes it. Old Oct-8 ocr_review gates are safe to answer.

Helpers (moved from the session scratchpad): workspace/_tools/ocr_resp.py (TSV region_id, text, [kind] -> validated ocr_review_response) and workspace/_tools/tr_resp.py (TSV region_id, text, speaker, addressee + meta JSON -> validated translate_response; skips ids not in request, reports omitted). visual_qa: every error pre-check needs a fix or `accept` (see [[mistakes-log]]).

Videos (done for now): Kimigazer Buon_ThuongNho + DeThuong_DungGu, Shuumatsu UBuon_LoiHua in videos/ (video, silent copy, project.json, caption.txt); Itsuka = user's own CapCut video. For translated series I only pick images + write captions from now on ([[video-mood-and-captions]]).
