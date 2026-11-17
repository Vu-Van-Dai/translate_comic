---
name: hitl-workflow-2026-11-08
description: 2026-11-08 the project switched to a human-in-the-loop workflow (Studio region review, Colab via Drive, Photoshop typesetting by the user); supersedes visual_qa/render QA
metadata:
  type: project
---

User's new workflow (2026-11-08), agreed decisions:

1. Download (unchanged) -> 2. detect+OCR on Colab T4 (local fallback) -> 3. USER reviews regions in
**Studio** (local browser app, NOT an .exe -- Smart App Control blocks unsigned exes): move/resize/add/
delete boxes, set kind (dialogue, thought, narration/caption, shout, sfx, logo, watermark, credit,
signage, ignore) -> 4. I check transcriptions only (dialogue, SFX, etc.; NOT logos/watermarks) and plan
cleaning -> 5. code cleans EVERYTHING, translated or not (dialogue local; SFX/logo/watermark via manga
LaMa `anime_manga_lama.pt` on Colab; PS Content-Aware Fill optional) -> 6. I translate dialogue + SFX
(pure translation); untranslated lines -> `untranslated.txt` (series, chapter, page, region id, kind,
bbox, source text) -> 7. code builds PSD: Original visible background, Cleaned above (opacity
adjustable), groups "Thoại" + "SFX" of editable text (Typer Tools style) -> 8. USER typesets/QAs in
Photoshop -> 9. code exports the EDITED PSDs to WebP q95 in library/ (+000 poster).

- I no longer do render/visual QA; my only QA is transcription accuracy.
- Colab: user is on FREE tier. **No ngrok** (1 GB/month cap; free-tier tunnel rules). Files go via
  Google Drive using **rclone** (runs only when called; user forbids boot-time background apps -- so not
  Drive for desktop). Colab MCP (official, uvx) only triggers/checks cells -- never move images
  through MCP (token cost). Models cached on Drive; persistent API/job key in gitignored `.secrets/`
  and Colab Secrets. Never write the ngrok token into repo or notebook.
- In-flight chapters (MID 11-27, NH ch.8, Kawaii 4-5, Xiao Gou, WitR) MOVE to the new workflow at
  the stage they reached.
- Content (my stance told to the user 2026-11-08): adult romance/sex/coerced scenes translated like
  any line -- redo MID skips ch.12 p41-88, ch.27 p2-81; non-sexual teen romance translated. Only
  sexual content involving minors is not transcribed/translated: those regions go to
  untranslated.txt with raw OCR text and are still cleaned. Supersedes the stricter 2026-10-28
  session policy in [[content-boundaries]].

**Update same day (user):** Studio = local web app (may be packaged as .exe later; user handles
blocking). SFX/logo/watermark cleaning: **Photoshop Content-Aware Fill FIRST**; LaMa/bigger inpainter
on Colab only where PS falls short or art must be reconstructed (trees, characters); pick the most
capable inpainter that fits a T4 (Anime/Manga Big-LaMa main; FLUX.2 Klein 4B candidate, untested on
T4). Step 6 translate per series (whole series at a time). Step 7: I insert dialogue first, user
refines + QAs. User asked me to route minors' sexual content to a GPT agent via AgentRouter with my
prompt -- I declined (no routing, no prompt); those regions are only marked/left blank. Never store
the AgentRouter key the user pasted.

**Why:** user wants to do QA/typesetting themselves and keep the agent to operator work.
**How to apply:** build/run stages in this order; see [[priority-batch-2026-11-01-resume]] for where
each in-flight chapter stood.

**Build state (end of 2026-11-08 session):**
- DONE: Studio (UI in Vietnamese, stored labels English -- user 2026-11-08) (`comic studio [--series S -c N]`, comic/studio/, tests/test_studio.py; writes
  analysis/page_NNN.json, detector copy in analysis/_detected/, reviewed pages in workspace
  studio.json; new TextKind `keep`, TextRegion `tag` + `edited`).
- DONE: Colab over Drive (comic/colab/: jobs.py protocol; models NOT pushed -- notebook runs fetch_models.py on Colab into Drive cache, worker.py runs in notebook, tasks.py,
  `comic colab push|status|analyze|stop-drive`; notebook colab/comic_gpu.ipynb, copy in
  G:\My Drive\comic_gpu). Drive for desktop mounts G:\My Drive, launch-at-login OFF, started on
  demand by drive_root(). VERIFIED on a real T4 2026-11-08: LaMa inpaint ok (~45 s round trip, mostly Drive), analyze = PC results identical (15/15 boxes + text). Traps fixed: Drive sync was paused (resume in tray); a folder created by Colab before the PC's upload duplicated -> PC creates comic_worker, notebook never does; Colab is Py3.13 -> rapidocr 1.4.4 installed --ignore-requires-python + OpenCV 5.0.0.93 pinned (else worse English OCR); worker runs as a subprocess so no session restart. User workflow: open My Drive/comic_worker/comic_gpu.ipynb, Run all.
- DONE (a): `comic check` + resume (comic/check.py, tests/test_check.py; never deletes, `skip` -> region.hold_reason).
- DONE (b): translate gate includes SFX (wants_translation in translation/gate.py; skips hold_reason),
  untranslated.txt per chapter + series (translation/untranslated.py, `comic untranslated`, written by resume).
- NH ch.8 (2026-11-08 evening): Studio-reviewed by user, checked (157 fixed), p34 scanlator note EXCLUDED,
  4 art-only boxes deleted, TRANSLATED (185 lines, 11 SFX left as drawn). NEXT for NH ch.8: clean + PSD.
- DONE (c,d,e partly): `comic clean` (comic/clean_stage.py): dialogue flat fill on the spot, artwork spots
  batched to Colab T4 in ONE job (local fallback). SFX -> PS Content-Aware Fill via sfx.translated_sfx (translated
  SFX regions become SfxItems automatically; untranslated SFX stay as drawn). PSD: Original visible bottom,
  Cleaned above, groups "SFX" + "Thoại". `comic ps publish` re-exports PSDs saved after their PNG (stale_exports).
  New-flow order per chapter: studio -> check -> translate -> clean -> ps build -> USER edits PSD -> ps publish.
  NH ch.8 was cleaned via old `comic render` (local LaMa) before `comic clean` existed; ps build run 2026-11-08 night.
  Logos/watermarks (ERASE) still cleaned by clean_page (flat/LaMa), not yet PS-first -- TODO.
- TODO next: (a) [done]
  open_ocr_review); (b) untranslated.txt export after translate; (c) clean stage: dialogue local,
  SFX/logo/watermark PS Content-Aware Fill first, LaMa job via tasks.inpaint for reconstruction;
  edited boxes need letters found by colour (mask may not cover them); (d) PSD layout: Original
  visible bg, Cleaned w/ opacity, groups "Thoại"/"SFX"; (e) `comic ps export` from EDITED PSDs to
  WebP; (f) migrate in-flight chapters; (g) Colab MCP (uvx googlecolab/colab-mcp) optional --
  worker polling already works without it; (h) docs/ + CLAUDE.md section for the new flow.
- Old-pipeline chapters lost their dropped (SFX) regions at ocr_review; re-derive from masks
  (photoshop/sfx does this) before Studio review of in-flight chapters.

**RESUME (user paused 2026-11-08 ~15:00, back ~17:00):** Colab session stopped by user. Next = item 1
(transcription-check gate incl. Studio's new/edited boxes w/o text), then 2-6 of the TODO list above.
User has not reviewed any chapter in Studio yet (busy). On return: user restarts the notebook (Run all).
Nothing of mine is running (Studio test server stopped; Drive for desktop left open by the user).

**2026-11-08 ~22:00: FIRST FULL RUN DONE -- NH ch.8 published** (33 pages + 000 poster, WebP, from PSDs;
11 user-saved PSDs re-exported). NH series translation now complete (ch.1-19) -> per [[notify-per-series]]
check SFX/logos for the series notice. Open: logos/watermarks -- PS fill where it works, BUT logos sitting on hard areas (over art/characters/textured backgrounds) must go to LaMa on the T4, not fill (user 2026-11-08: fill cannot remove them cleanly); ps build planning ~7 min;
docs/CLAUDE.md for the new flow; resume's "next: comic render" hint. NEXT TASK: MID slice-order fix
([[comix-slice-order-bug]]) -- user approved shutting Colab meanwhile.
