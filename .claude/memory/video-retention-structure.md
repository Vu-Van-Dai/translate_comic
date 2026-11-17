---
name: video-retention-structure
description: "2026-10-27 operator's 4-beat structure for translated-series and review videos (hook / setup / highlight / teaser, motion on every shot), goal = views + retention; posted to FB+TikTok+YouTube on the channel schedule's open days"
metadata:
  node_type: memory
  type: feedback
  originSessionId: 3b780871-2a78-4840-ab3a-9a3b7dc17490
  modified: 2026-10-27T06:46:08.620Z
---

Operator, 2026-10-27: videos for series we translate (and, later, review videos of series they send links for) must drive views and watch time.

1. **Hook** -- the most striking panel that shows the situation at a glance, a short opening line written for THIS series (never a stock line), motion from frame one, <= 3 s.
2. **Setting the scene** -- 2-3 panels with short, easy-to-read subtitles.
3. **Highlight** -- the key reaction / line / plot turn; faster cuts here.
4. **Conclusion + teaser** -- a memorable moment, optionally the series title or a "next part" prompt.
Motion on every shot, never static image swaps.

Coded as `"structure": "retention"` in the video plan (VideoPlan.check_structure, gate refuses a plan missing a beat); rules in docs/video.md "Retention structure". It supersedes the old "no on-screen text" lesson for these videos (see [[video-feature]]).

**Posting:** finished translated-series videos go to Facebook (Rồng Nhỏ), TikTok and YouTube on the **open days** of workspace/_logs/channel_schedule.json -- those days are reserved for them (see [[channel-videos]]). Confirm the concrete list before scheduling.

**Review videos:** the operator will send series links; do not start them until asked.

**Why:** the primary goal is reach -- viewers must be hooked and kept.
**How to apply:** every new translated-series/review video plan uses the retention structure; check open days before scheduling. Captions per [[video-mood-and-captions]].
