---
name: video-picks-2026-11-14
description: "2026-11-14 user: I pick images + write captions per video (user applies own templates); counts per series; tool video_pick.py; progress"
metadata:
  type: project
---

User 2026-11-14: make videos from OCR-reviewed series. **My job = choose images + write captions only**; the user applies
their own templates (no `comic video render`). Counts: short manga ~2, longer manga 4-5, manhua/manhwa 8-10. Vary themes
(funny, sad, review, cool/badass, cute); per series 2-3 character videos (e.g. cute side) + 2-3 couple videos.
Rules from docs/video.md still hold: text-free art only (photoshop/NNN_art.png) -- never our Vietnamese pages; whole
scenes; hook/setup/highlight/end; captions are our own words; quotes use the series' PINNED pronouns; minors never framed
romantically (no couple videos for Dare mo, Kiss Shite Sugar-kun, Ku Ku, A Second Goodbye's school timeline).
Avoid/crop pages still showing group marks (Kawaii ch.4 jmcomic line, ch.5 green stamp) and recruitment posters.

Tool: plan JSON in workspace/_tools/video_plans/<series>.json -> `python -I workspace/_tools/video_pick.py PLAN` writes
videos/<series>/<name>/NN_cXpYYY.png + captions.txt (videos/ is a junction to D:\project tranlate\videos).
Thumbnail sheets: scratchpad artsheet.py SERIES OUTDIR (8x4 per sheet, labelled cX pYYY).

Progress 11-14 night: DONE Kawaii (2), JJ (2, present-day only, no ch.3 flashback, no 5.5), Neglected Husband (2, ch.8 only), Ku Ku (2, no romance), A Second Goodbye (5). User OKed the format. DONE MID (8: mid_a.json V1-V3, mid_b.json V4-V8). DONE XG (9: xg_a V1-V5, xg_b V6-V9). DONE Destined (9: destined_a V1-V4, destined_b V5-V9). DONE WitR (2). Kiss Shite Sugar-kun: SKIPPED by me 11-15 (sexual content of minors is central; told user, user may do it). ALL video picks done: 41 videos (Kawaii 2, JJ 2, NH 2, KuKu 2, ASG 5, MID 8, XG 9, Destined 9, WitR 2). Dare mo: USER DOES IT THEMSELVES (11-14). video_pick.py + artsheet.py now fall back to cleaned/ then original/ (webp ok).
Next: JJ (2), Neglected Husband (2), Dare mo/Kiss Shite/Ku Ku (2 each, no romance), ASG (4-5), MID/XG/Destined (8-10),
WitR (2, 1 ch reviewed). Destined/WitR have no _art.png yet (no PSDs) -> use cleaned/ or original.
