---
name: cpu-limit
description: Pipeline CPU load must stay at 80-90% max, never above 90%; keep LaMa (more accurate) on OpenVINO CPU
metadata:
  type: feedback
---

The user (2026-10-12) allows the pipeline 80-90% total CPU, never above 90% -- 100% lagged their desktop. `comic/cpu.py` holds this: COMIC_CPU_SHARE default 0.75 of logical cores, split across workers, below-normal priority. Verify with `typeperf "\Processor(_Total)\% Processor Time"` after changing anything heavy.

LaMa stays the inpainter (user: "if it's more accurate, use it"); it runs on OpenVINO CPU because the Iris Xe GPU cannot run it correctly (DirectML rejects a MatMul; OpenVINO GPU f16 gives garbage). Text detection runs on GPU via onnxruntime-directml.

**Why:** user's explicit limits. **How to apply:** don't raise thread counts or worker counts past this budget. See [[no-background-processes]].
