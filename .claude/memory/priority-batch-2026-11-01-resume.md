---
name: priority-batch-2026-11-01-resume
description: RESUME HERE (2026-11-01 ~18:30, user said "stop all and continue later"; nothing running) -- exact state of NH, MID, queues
metadata:
  type: project
---
Continues [[priority-batch-2026-10-31]] (read its tail for workflow/tools). ALL python killed; no queues running.

**When I Tried to Enjoy the Life of a Neglected Husband (NH):** PUBLISHED ch.1-7, 9-19 (topmark done 16-19, 7). Left: **ch.8** -- fresh analysis at ocr_review; notes REWRITTEN from scratch in workspace/_tools/gate_notes/nh8.txt (done, not run yet). Next: copy nh_names19.json -> nh_names8b.json with _extra=nh_extra8b.json (write summary: haunted-cottage test of courage, Allen's mirror prank from 10 yrs ago, Allen calls Colin a little brother, Allen's fever + Chris's herb infusion, promise to return next autumn, Chris conflicted; back at Red Rose Palace, tea souvenirs, knitting a blue scarf for Allen's winter birthday, Nathaniel will give one too) -> `python workspace/_tools/gate_run.py "<NH>" 8 nh8.txt nh_names8b.json` -> then add p029 manual region (r92 erase + r93 narration, bbox 995,1427,1467,2193, "THE TRADITION IS THAT ON THE NIGHT BEFORE THE BIRTHDAY OF YOUR LOVE YOU GIVE THEM A SELF-MADE SCARF" -> "TRUYỀN THỐNG LÀ ĐÊM TRƯỚC SINH THẦN NGƯỜI MÌNH YÊU, TA TẶNG NGƯỜI ẤY CHIẾC KHĂN TỰ TAY ĐAN") -> also p003 "HOW DOES HE" needs addbox erase ~670,470,970,655 -> render, review, qapass, resume, topmark. Images in ch.8 original may be truncated: use PIL ImageFile.LOAD_TRUNCATED_IMAGES=True for ov.py. Then NH translation is COMPLETE (then SFX + logos, then one series notice per [[notify-per-series]]).

**My Insatiable Demon (MID):** PUBLISHED ch.0-10. ch.11: fixes applied (p10/39/43), QA passed once but resume says 50 pages not shown as they stand -> run `qapass` + `resume -c 11` again (re-renders, then qapass+resume once more). Translated, NOT rendered: ch.12-27 (status running) -> render each, review (CHECK SPEAKERS from faces -- ch.7 had demon lines given to Wonyoung; check missed short bubbles like WHAT?!), qapass, resume. ch.28-29 at ocr_review (translate next; ch.28 follows ch.27 coerced scene -- check consent). ch.30+ partial: rerun `COMIC_CPU_SHARE=0.6 python scripts/analyze_all.py --series "My Insatiable Demon"` (was interrupted).
Content skips logged in workspace/_logs/content_skips.log: MID ch.12 p41-88, ch.27 p2-81 (coerced). Earlier ch.0, ch.6 p30-48 too.
Open question for user: MID ch.22 p12 source says J "turned 40", kept 30 per ch.6.
**Other series pending:** Xiao Gou 4-48+extras and WitR 4-93 analysis (analyze_all, qd_aa_Xiao_Gou log was mid-run when killed), Kawaii 4-5 translated not rendered (queue_1101e never ran), then SFX pass + logos per series.
Restart plan: one heavy job at a time in background (render MID 11-27 sequentially; analyze_all MID, Xiao Gou, WitR), translate in parallel.
