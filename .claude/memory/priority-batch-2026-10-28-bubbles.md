---
name: bubbles-2026-10-28
description: Bubble-shape model + mask/size fixes from the user's Kimi ch.1 p.3/8/17/18 report; library not yet republished
metadata:
  type: project
---
2026-10-28: user reported (Kimi ch.1) text spilling/misaligned in dashed thought bubbles (p.8), big/small text mismatch in one bubble, spiky bubble erased + free text half-erased + a drawn face erased (p.18), credits+logo and site watermark on p.3.
Built: comic/vision/bubbles.py (YOLOv8-seg speech-bubble ONNX, models/bubble_seg_yolov8m.onnx, onnxruntime, AGPL-3.0; ultralytics .pt route was denied by the permission classifier -- don't retry). mask._limit_by_bubble (leak = fill >= 3x model bubble -> shrunk bubble; far reach cut at 15px; failed fill -> shrunk bubble); mask.refine_page_mask (drop enclosing outlines, complete half-marked strokes, textured free text cleans its box); clean.erase_mask keeps only marks within 8px of the box (protects doodles); render._share_bubble_caps + one-bubble-one-size (<=1.15x, no growth); kind ERASE clears its whole box; erase_marks --box, detects on originals, idempotent.
Kimi ch.1: library watermarks erased in place (all 31 story pages + p.3 credits block). Pages 3, 8, 17, 18 re-rendered in workspace -- NOT yet in library; ch.1 sits at visual_qa round 2. To publish: pass QA (it re-renders pages whose analysis gained erase regions, then publishes PNG over the in-place-cleaned library pages -- fine, they include the erase).
