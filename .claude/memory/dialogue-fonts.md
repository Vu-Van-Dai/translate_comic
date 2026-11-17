---
name: dialogue-fonts
description: "Operator's dialogue-font rule (2026-10-31) -- fit narrow balloons by scaling AnimeAce, never by switching faces; Wildword2 TB / AnimeAce 3 / Chinacat are the preferred dialogue faces"
metadata:
  node_type: memory
  type: feedback
  originSessionId: 5f67263d-979c-4f5e-9fa0-9bfe73e95ced
  modified: 2026-10-31T03:54:18.180Z
---

When dialogue does not fit (narrow vertical CJK balloons, Kawaii ch.2), scale AnimeAce (horizontal/vertical scale, like Photoshop's Character panel) instead of switching to another face such as Felt. Switching faces mid-series is "jarring or unpleasant to look at".

Preferred dialogue faces (editmanga "Chọn font typeset cho newbie", which the operator showed): **Wildword2 TB, AnimeAce 3, Chinacat** (the best three; veteran editors stick to them). Other categories from that guide: secondary speech Augie / SkippySharp / Chitchat TB; SFX Piekos FX Basic/Thin, Astounder Square, Indie Star, Sign Language; narration Blambot Pro Lite, CCDigitalDelivery, Digitalstrip 2. Only AnimeAce3 is in assets/fonts as of 2026-10-31.

**Why:** the operator's taste and Vietnamese scanlation convention.
**How to apply:** keep one dialogue face per series; fix fitting with condensing (render hscale), margins, wraps or shorter lines, not a face change. Related: [[mistakes-log]].
