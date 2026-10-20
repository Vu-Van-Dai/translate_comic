# Videos (`comic video`)

Read this before running any `comic video` command or making channel videos.

Moved here from CLAUDE.md word for word (2026-10-20) so the everyday rules stay
short; CLAUDE.md links to this file. The rules below are as binding as CLAUDE.md's.

## Videos (`comic video`) -- an add-on, not part of the pipeline

Short 9:16 videos (25-40 s, the operator's length) made from a chapter that
has been translated. Translation stays the project's purpose; nothing here
writes to the pipeline's artifacts or to `library/`.

```
comic ps art --series NAME -c N        # text-free art from built PSDs (build now saves it too)
comic video plan --series NAME -c N    # panels + sheets, opens video_plan (exit 10)
comic video render --series NAME -c N  # validates the plan, renders, opens video_qa
comic video plan|render|publish ... --name Arc_01-10    # a named video hosted in chapter N
comic video publish --series NAME -c N [--name NAME]  # -> videos/<series>/<chapter or name>/
comic video styles | comic video music
```

- **Never our Vietnamese translation in a video** (the operator, 2026-10-13;
  clarified 2026-10-14: the source's own lettering, English or otherwise, is
  fine, and the original is now the default -- text-free art leaves empty
  balloons).
  `video/source.py` allows only `photoshop/NNN_art.png` (dialogue, translated
  SFX and watermarks erased, nothing set), `cleaned/`, or the original page;
  it refuses `translated/`, `library/` and the Photoshop previews, and the
  renderer opens every image through it.
  **Per-series exception:** `video.yaml: allow_translated: true` lets a
  series show its finished `library/` pages (Mi Imitador, the operator,
  2026-10-14); `check_allowed` reads the switch from the path's own series,
  and those pages become that series' default source.
- **Same gate split as the pipeline.** Panels, frames and encoding are
  deterministic; the agent picks panels, crops (the sheets carry a 100 px
  grid), order, durations, motion, transitions, captions, music and the post
  caption in a closed vocabulary (`video/schemas.py`), and checks a frame
  sheet at `video_qa`.
- **Styles** live in `profiles/_presets/video_styles.yaml` (story, beat,
  melancholic, emotional, drama, comedy, tense, teaser, romantic). `story`
  and `beat` are measured from 10 reference edits
  (`scripts/ref_analyze.py`, `workspace/_references/tiktok`): one channel
  holds even 2.5-3 s shots with hard cuts; the other re-cuts 2-3 panels every
  0.6-1 s on a 120-150 BPM beat with flips and glitches.
- **A series' rules** go in `profiles/<series>/video.yaml`: who is a minor,
  forbidden styles. Never nudity or romantic framing of a minor -- Super
  Lovers forbids `romantic` while Ren is a child.
- **Music** from `assets/music/library.yaml` (licence and credit per track;
  a credit goes into `caption.txt`). Every render also writes
  `video_silent.mp4` plus `music_cue.txt` (cut times), for a sound added in
  the TikTok app.
- **What the operator rejected (v1, 2/10) and what replaced it.** v1 had tight
  face crops, a caption on every shot and royalty-free library music -- none
  of which real BL edits do. Measured over ~40 references (the two channels'
  most-viewed posts plus #blmanhwa / #manhwabl / #danmei, 5-23M plays):
  whole panels or scenes, untouched (empty balloons and all), on white;
  a hard cut every 4 beats (~2.4 s) of a trending song; **no on-screen
  text** -- the hook goes in the post description. Default to that recap
  format (style `story`, `background: white`); frame each shot on a whole
  scene and around the empty balloons, never tight on a face.
- **Music is the operator's call: trending songs are fine** (videos are not
  monetized, 2026-10-14). `scripts/ref_fetch.py` saves each reference's
  TikTok sound to `workspace/_references/sounds`; tracks registered as
  `assets/music/trending/tt_*`. TikTok serves AAC under an `.mp3` name, so
  audio is decoded through FFmpeg. Pick the same sound in the app when
  posting. The CC BY library stays only as a fallback.
- **CapCut templates** (`capcut.com/vi-vn/template`, single slash -- the
  double-slash link 404s): the site's `get_similar_templates` API lists
  templates with preview video, length and use count, but filling one needs
  the CapCut app and a sign-in, so it is the operator's manual route.
- TikTok search needs a login; logged out it returns the For You feed.
  Collect by profile or `/tag/<name>` pages, and filter by author/caption.
- **Character showcases and other chapters.** A shot may name another
  chapter of the series (`chapter`), whose page is cut into panels on first
  use -- the operator prefers drawing the best images from several chapters.
  `panel2`/`crop2`/`motion2` stack a second image under the first (the
  split screen of CapCut BL edits, @muoivungvietnam). The `beat` style
  re-cuts 3-6 photos every 2 beats; no mirror flips on original pages (the
  lettering reads backwards). A showcase never centres a character who is a
  minor in the chosen chapters: Super Lovers' Ren is a child until much
  later, so its showcases are Haru's (22 from Ch.2; 17 in Ch.1, so not Ch.1).
  `scripts/page_contact.py` draws thumbnail sheets for picking pages.
- **Named videos** (`--name`): one chapter may host several videos (arc
  recaps, highlights, showcases); each keeps its own plan, gates and output
  under `video/<name>/` and publishes to `videos/<series>/<name>/`, while
  panels stay shared per chapter. Caption role `note` is the small
  narration CapCut BL edits lay across a split screen's seam.
- **Webtoon traps** (Mi Imitador, 15 videos): a library page often stacks
  2-3 panels, so a whole-page shot renders each tiny -- crop to one panel;
  trim the "to be continued" footer; emoji do not render in the caption
  fonts; split halves are 1080x960, so crop near 1.125:1; a seam note must
  not cover the art's own bubbles.
- **Measured traps** (Super Lovers ch.2 pilot): a strong warm tint turns
  black-and-white manga into a yellowed scan, so tints stay subtle; a
  full-frame crop past ~6x upscale of a 730 px scan is mush
  (`gate.MAX_UPSCALE`, p.34 at 7.7x); empty balloons sit at panel edges, so
  crop them out; a hug crop that loses the faces loses the beat.
- **Channel folders** (`comic/video/folder.py`, the operator's own image
  folders): 11 recipes. `caption` is the one with text -- measured on the
  operator's reference page (Lazy Kids Team reels, 2026-10-15): full-frame
  crops, a joking narration line on every shot, 2-4 s holds, zoom-blur /
  glitch / black-dip switches. Captions are the agent's, per image
  (`scripts/folder_custom.py` + a plan JSON); `assign` never picks it.
  Posting: `scripts/channel_post.py --platform all` (TikTok Studio, Meta
  Business Suite, YouTube Studio through the project Edge on 9223).
- FFmpeg comes from winget (per-user, found even before PATH refreshes);
  frames are drawn in Python and encoded by Intel Quick Sync (`h264_qsv`),
  x264 as fallback. A 33 s video renders in about two minutes.
