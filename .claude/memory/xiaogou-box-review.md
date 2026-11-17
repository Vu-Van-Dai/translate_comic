---
name: xiaogou-box-review
description: 2026-11-11 the user asked ME to do the Studio box review of Xiao Gou Diu Le Hun ch.4-48 (not them); method, standard, progress
metadata:
  type: project
---

2026-11-11 the user: "check the OCR box for that set for me" -> I do Xiao Gou ch.4-48 box review myself
(Studio stopped so a stale tab cannot overwrite). Method as MID: `python -I workspace/_tools/boxsheet.py
"Xiao Gou Diu Le Hun" N $B/cNN` -> read sheets + cNN_regions.txt -> edits file -> `boxedit.py ... --review 1-LAST`.
Edits kept in scratchpad bx/edits/cNN.txt (session temp; may be gone).

Standard used (ch.4): delete empty junk boxes on art/faces; merge split "!"/"?" pieces and split lines into their
bubble's box; SFX (骨碌碌, 哐啷, 啪, 嘻嘻嘻, 唔唔唔, 转身, 推, 泪汪汪) -> sfx, one box per SFX; floating spoken lines
-> dialogue; thought bubbles -> thought; signage (posters, shop signs) -> keep; bare punctuation/"......" on art
deleted; title/credits page (p002 小苟丢了魂 logo + 编绘/编辑/出品) and cover junk: delete all boxes; end promo pages
(投喂榜 prizes, 活动规则, 点赞收藏 / QQ群 page) delete all boxes (operator's front/back matter).
Also: inner-thought bursts -> thought; a "......" bubble with no box -> +dialogue:x1,y1,x2,y2:...... (ch.25+).
Add missed big SFX with +sfx:x1,y1,x2,y2 (sheet scale 0.4, y ticks every 200 px).

Progress: DONE ch.4-48 ALL (+13.5, 27.5, 28.5, 33.5, 42.5); resumed 2026-11-11 afternoon (user: "continue the box QA while waiting"). BOX REVIEW COMPLETE 2026-11-11. Cleaned so far: 4-40 (incl .5s); 41-48 CLEANED on T4 2026-11-11 (all chapters cleaned) (ask user to open Colab first). Then tell the user; check/translate
only when they say go. See [[resume-2026-11-11]], [[hitl-workflow-2026-11-08]].
