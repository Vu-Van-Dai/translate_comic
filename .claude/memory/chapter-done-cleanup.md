---
name: chapter-done-cleanup
description: "2026-11-14 user: when a reviewed chapter is exported to the library, delete its workspace images/PSDs + raw (like Mi Imitador); tool workspace/_tools/finish_chapter.py"
metadata:
  type: feedback
---

When the user says a chapter's PSD review is finished: export it (`comic ps publish`, WebP q95, into library/ which now
lives on D:\project tranlate via junction), then free its space like Mi Imitador: keep workspace text data (chapter.json,
analysis/*.json, translation.json, handoff/*.json, untranslated.txt, studio.json), delete every image/PSD in its
workspace folder and its raw/<series>/<n>/ download. Tool: `python -I workspace/_tools/finish_chapter.py SERIES CH`
(dry run) then `--apply`; it refuses unless every published page is in the library as WebP.

**Why:** E: (260 GB) filled up 2026-11-14 and stopped the PSD builder; reviewed chapters' images are no longer needed.
**How to apply:** user reviews smallest series first; I open each chapter's PSDs in Photoshop for review
(`Start-Process Photoshop.exe <psds>`). Exception: Dare mo ch.4 -- user exports the whole chapter themselves;
I do not open/export/convert pages 1-17 (sexual content, minors). Related: [[finalize-without-review]], [[resume-2026-11-14]].

**Recruitment/ad pages at a chapter's end (user, 2026-11-15, confirming 11-13):** publish them with the chapter as drawn
(no exclude, no question); they are deleted by hand from library/ afterwards. Don't pause the export to ask about them.
Done 11-15: Kawaii ch.2 published (33 WebP) + cleaned up (freed 462 MB).
- Kawaii ch.1 published (39 WebP, 29 edited PSDs re-exported) + cleaned up 11-15.
- Kawaii ch.3 published (38 WebP, 35 re-exported) + cleaned up 11-15 (517 MB).
- Kawaii ch.4 published (30 WebP, 27 re-exported) + cleaned up 11-16.
- Kawaii ch.5 published (31 WebP, 28 re-exported) + cleaned up 11-16 -- Kawaii series (ch.1-5) DONE.
