---
name: colab-drive-account
description: "Colab account in use -- 2026-11-13 22:25 J: = cocphieuluu (code+models pushed); set COMIC_DRIVE to the active Drive"
metadata:
  node_type: memory
  type: project
  originSessionId: 24b5e041-b178-4e20-948b-5c2f729f6c34
  modified: 2026-11-13T15:27:30.123Z
---

2026-11-11: the first account (Drive G:) ran out of Colab GPU, so the user switched Colab to the second account,
whose Drive for desktop mount is **H:\My Drive** (both accounts are signed in; the DriveFS log names each mount).
I copied comic_worker (code, models 298 MB, notebook, pushed.json; not the old jobs/worker.json) to H: and ran
`colab push` there.

**How to apply:** `jobs._find_mount` tries G: first, so prefix every Colab command (push/status/clean,
colab_clean.sh) with `COMIC_DRIVE="H:/My Drive"` or it talks to the old account's empty worker. If the user
switches again, copy the folder the same way.
**2026-11-11 21:40: back on the FIRST account (G:)** -- H: GPU ran out. Use COMIC_DRIVE="G:/My Drive" (wait_colab_clean.sh now defaults to G:). Colab sessions last ~2 h. See [[colab-only-lama]], [[hitl-workflow-2026-11-08]].
**2026-11-12 ~08:30: third account vudaisek18.1 = Drive I:** (G: GPU running out). Copied comic_worker (code, models,
notebook, pushed.json) to I: and ran `colab push` there -> use COMIC_DRIVE="I:/My Drive" (wait_colab_clean.sh now defaults to I:).
Mounts: G: vudaiktpmk18, H: kaidouraichi, I: vudaisek18.1.

**2026-11-13 ~10:45: kaidouraichi (H:) GPU used up -> back to vudaiktpmk18 = G:.** Use COMIC_DRIVE="G:/My Drive". G: has the
LaMa/detector/OCR models; new worker code (batched LaMa, LAMA_MAX_SIDE 4096) pushed there. Notebook cells identical on G:/H:.

**2026-11-13 22:25: cocphieuluu = Drive J:** (user signed Temnoava out; cocphieuluu took the same letter, so every
script with COMIC_DRIVE="J:/My Drive" works unchanged). comic_worker ready there: code pushed (91 files), notebook +
models (298 MB) copied from G:. User runs the notebook the morning of 11-14 and reports.
Mounts now: G: vudaiktpmk18, H: kaidouraichi, I: vudaisek18.1, J: cocphieuluu.
