---
name: colab-only-lama
description: Only LaMa inpainting goes to the Colab T4; everything else (analyze/OCR, masks, layout, PS planning) runs locally
metadata:
  type: feedback
---

Send only LaMa (inpainting) work to the Colab T4 -- `comic clean` batches a chapter's artwork spots into one
job. Keep everything else on the PC: detection + OCR (`comic analyze`, not `comic colab analyze`), masks,
bubble/centre/fit layout, Photoshop planning/build, export.

**Why:** the user (2026-11-08): Colab's T4 machine has a weak CPU, so CPU-bound steps run slower there than
locally; only the GPU model is worth the Drive round trip (~45 s per job).
**OCR rule (user, 2026-11-08): use Colab for OCR only where the GPU makes it faster end to end.**
Measured 2026-11-08 on 5 English pages: local `comic analyze` 26 s vs `comic colab analyze` 84 s
(21 s compute + ~60 s Drive) -- identical output. RapidOCR (en/zh/ko) is CPU on both sides, detection is
already on the local iGPU (<1 s/page) -> stay local. Japanese (manga-ocr, a transformer) may win on the T4
for long chapters: time both on the first Japanese chapter and keep whichever is faster.
**How to apply:** default to local commands; `comic colab analyze` only where measured faster or asked. See
[[hitl-workflow-2026-11-08]].
