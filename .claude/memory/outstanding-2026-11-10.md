---
name: outstanding-2026-11-10
description: Complete outstanding-work list as of 2026-11-10 evening (chapter status per series + open tool tasks); source workspace/_logs/survey_2026-11-10.json via workspace/_tools/survey.py
metadata:
  type: project
---

Snapshot 2026-11-10 evening (user asked for it before the night session: translation + SFX for several series).
Re-run `python workspace/_tools/survey.py --json workspace/_logs/survey_<date>.json` for a fresh table.

**SFX test: DONE** (NH ch.8, see [[sfx-erase-strokes]]). Open from it: dot-pattern fill ([[clean-screentone]]), Studio brush.

**1. OCR not done (batch stopped 16:30):** Walking in the Rain ch.20-93 (75 ch; ch.19 may need re-run). Super Lovers 17 ch
(8-10,12,15,17-18,32,34,37-38,40,42,45-46 -- series ON HOLD, old pipeline). Resume: analyze_all.py same command.

**2. Need Studio box QA (user reviews; I did MID):** A Second Goodbye 6-22 (17) · Benming 1-8 (8) · Dare mo 4 ·
Dasadanan 2-33 (34) · Destined 1-84 (85) · Golden Light 1-47 (47) · Ju Jue 4-5 (3 folders) · Kiss Shite 4 ·
Ku Ku Lian 1-4 (5 folders) · Super Lovers 38 (hold) · WitR 4-19 (16) + ch.3 p8-10 · Xiao Gou 4-48 (50).

**3. Reviewed, not translated:** A Second Goodbye 1-5 · Kiss Shite 1-3,5-6 · MID 28-29,31-46 (WAIT for user's go).

**4. Translated, not published (new flow: clean -> ps build -> user PSD review -> ps publish):** Dasadanan 1 (PSDs built) ·
Ju Jue 3 · Kawaii 4-5 · MID 11-27 (re-cut pages need check + carry_translations + translate --missing first) + MID 30 ·
Super Lovers 3,4,6 (hold) · NH 8 (PSDs built + SFX test; user review -> publish WebP).

**5. Published but SFX left as drawn (old flow):** Dare mo 1 · Itsuka 4-5 · Kimigazer 1,6 · Shuumatsu 7-8 · Super Lovers 1,3-4,6 ·
WitR 3 (also must be REPUBLISHED after re-cut) · NH 1. Mi Imitador 1,4-6,8-10,13,15-16: finished, NOT redone (CLAUDE.md).

**6. Logo/watermark/credit regions:** unpublished with marks: A Second Goodbye 1,4,5 · Kawaii 4-5 · Kiss Shite 1-5 · MID 11,16.
Published chapters with marks (erased by the old box erase, not checked by eye; "hard logos -> LaMa later"):
Kawaii 1-3 · Kimi ga 1-3 · MID 1,3,7,10 · WitR 2 · NH 2,5-19 · Xiao Gou 1-3.

**7. Tool/other tasks:** dot-screentone pattern fill · Studio brush/eraser for erase masks · stroke mask for dialogue/logos
(`with_erase_boxes` whole box) · YouTube: duplicate draft "edit Như Ngày Hôm Qua Sơn Tùng M TP" (ask before deleting),
free days 2027-04-03..15 need videos · TikTok/FB channel_post re-run due (every 2-4 weeks) · per-series done notices
([[notify-per-series]]) · docs/ + CLAUDE.md for the new flow.
