---
name: notify-per-series
description: "2026-11-01 -- report to the user only once per series, when the WHOLE series is done (translation + SFX + logo/watermark removal); no interim updates"
metadata:
  node_type: memory
  type: feedback
  originSessionId: 09b1bc9c-97e4-4339-bd3d-2160c5cfede8
  modified: 2026-10-31T17:22:17.664Z
---

User (2026-11-01): "you only need to provide a single notification once the translation of an entire series is complete -- this includes handling both the SFX and the logos."

**Why:** the user wants to work uninterrupted; partial-progress reports are noise.
**How to apply:** keep working through the queue silently. A series counts as complete only when every chapter is translated, rendered, QA-passed, published, the SFX pass is done (docs/photoshop.md) and logos/site watermarks are removed (erase_marks) on every library page, held/untranslated pages included. Then send ONE short notice for that series. Still interrupt for real decisions, errors or pronoun changes ([[work-autonomously]]). End-of-batch table: [[untranslated-summary]].
