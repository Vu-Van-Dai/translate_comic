---
name: video-feature
description: TikTok-style chapter video add-on (proposed 2026-10-13) — the user's rules for image sources, audio, when to make videos; status of the plan
metadata:
  type: project
---

On 2026-10-13 the user asked to extend the project with short TikTok-style videos made from a chapter's images. Translation stays the core purpose; video is an add-on stage, never a replacement.

The user's rules:
- Images must be raw (untranslated) or have the dialogue removed. **Never use images containing translated dialogue** (no `translated/`, Photoshop previews or `library/` pages).
- Pick 4–5 or more images. Choose them for visual appeal, story importance, expressions and key moments, and so that together they tell a short, coherent story.
- Background music only. No voice-over or narration.
- I decide shot order, durations, zoom/pan/motion, transitions, on-screen text and its timing, music, the hook/title and the ending.
- Tools: CapCut (9.6 is installed; its drafts are plain JSON), my own compositions, or existing CapCut templates.
- Not every chapter gets a video. The user may ask for one together with a translation. I may suggest one when a chapter has standout scenes, and the user decides.
- Reference channels (BL edits): tiktok.com/@baoboidz and tiktok.com/@jaeha_bl. TikTok serves only a "Please wait" shell to scripts, so studying them needs the user's browser or saved videos.

Decisions (2026-10-14, the user): image source = text-free Photoshop panels or dialogue-removed panels (SFX / original comic elements may stay); audio = a suitable music track AND a silent version; branding = none; duration 25-40 s; references = 5-10 sample videos the user put in a folder; pilot = Super Lovers Ch.2 (approved); original dialogue may be quoted occasionally as a caption; more styles wanted (melancholic, emotional, ...). Coding started 2026-10-14. Built the same day: `comic video plan|render|publish`, `comic ps art`, 9 styles, 24 CC BY tracks; pilot SL Ch.2 published to videos/Super Lovers/Chapter_002 (33.5 s, 11 shots, emotional, Lasting Hope). Reference edits saved in workspace/_references/tiktok (fetched through the user's Edge; the user expected me to download them myself, not to save them by hand).

**Why:** the user wants promotional clips for the translated series without weakening the translation pipeline.
**How to apply:** keep videos in their own stage and folder (not `library/`). Enforce the no-translated-dialogue rule in code. Never frame a character who is a minor in that chapter romantically, and never select nudity of a minor (Super Lovers: Ren is a middle-schooler in Ch.2). Trending songs are allowed (user, 2026-10-14: not monetized); sounds come from the reference TikToks. See [[super-lovers-finish]] and [[work-autonomously]].

**User verdict on the pilot (2026-10-14): 2/10, "posting this would invite negative comments."**
- Music: the royalty-free library sounds strange and amateur next to real TikTok edits. Copyright is not a concern for the user (videos are not monetized): use trending songs / songs with lyrics, as BL edit channels do.
- Images: don't crop tightly; full images are fine. Photo selection was weak.
- Text overlays: unnecessary; put the text in the post description instead (overlay optional).
- Transitions were OK.
- Use existing CapCut templates (capcut.com/vi-vn/template) or study many more BL edits before building my own style again.

v2 (2026-10-14) after research: recap format -- whole scenes on white, cut every 4 beats (2.41 s) of "Who Says" (the sound jaeha_bl used on their 609K-view post), no on-screen text, hook in the description. 12 shots, 29.9 s, published to videos/Super Lovers/Chapter_002. Awaiting the user's verdict. Next ideas if still weak: baoboidz format (2-3 images, long holds, Vietnamese sound, template-like effects), or the user applies a CapCut template to my ordered images.

Source rule clarified (user, 2026-10-14): only OUR Vietnamese translation is forbidden in videos. Original pages in any language (English scanlation, English translation, raw Japanese...) are fine, and are the default now -- they avoid the empty-balloon look of text-free art.

2026-10-14 later: user liked the recap ("much better") but wants more movement: snappy beat edits of 3-5 cute/cool photos of the main shou or gong; reference style @muoivungvietnam (CapCut BL edits, split screen, 17-19 s); images may come from several chapters. Built: per-shot `chapter`, split screen (`panel2`), and a Haru showcase (Ch.3/5/7, One Kiss, 25.4 s) in videos/Super Lovers/Showcase_Haru. Declined a Ren showcase: Ren is a minor through these chapters.

User feedback on the first Haru showcase (2026-10-14): too many jarring cuts, and a crop cut half the face. Rules: hold each photo 6-8 beats with gentle motion; crop to the frame's own 9:16 shape centred on the face (cover never trims a face); wide shots go into split-screen halves. Captions optional -- skip them when a video already has many images. User argued BL romance is unavoidable; I agreed romance is fine between adults but kept the line on romantic/couple framing of a character who is a child in the story (Ren in early Super Lovers).

2026-10-14, later: **Mi Imitador is an exception -- its Vietnamese translation may appear in videos** (user: "keeping the Vietnamese translation as-is is fine"). Coded as `profiles/Mi Imitador/video.yaml: allow_translated: true` (read inside `source.check_allowed`, so library pages become that series' default source). Every other series still forbids our translation. Made three Mi Imitador videos on my own choice of theme, published with `comic video publish --name`: `videos/Mi Imitador/Intro` (Ch.1-2 premise + Ch.46 "Em thích anh.", How You Get The Girl), `KanghyunJiun` (romance across Ch.18/23/46/53/60, Moments Like This), `HaeyongYoonjae_Wreath` (Ch.48 comedy, Who Says). Awaiting the user's verdict. Lesson: the `romantic`/`emotional` styles' always-on `warm` effect tints the white background cream -- I used `story` with explicit fades instead.

2026-10-14, later still: user asked for 6 arc videos (10 chapters each), 4 standout-chapter videos and 2 @muoivungvietnam-style edits for Mi Imitador -- all made and published in `videos/Mi Imitador/` (Arc_01-10 ... Arc_51-60, Highlight_Ch13_PuppyWalk / Ch29_Snow / Ch45_Confession / Ch59_Beach, Split_KanghyunJiun, Split_HaeyongYoonjae). User said: finish videos before resuming translation. Built for this: `--name` on plan/render/publish (several named videos per host chapter), caption role `note` (small narration over the split seam, max 200 chars). Lessons: webtoon pages often stack 2-3 panels -> a whole-page crop renders them tiny, always crop to the one panel; the "to be continued" footer must be trimmed; emoji do not render in the caption fonts (keep them in caption.txt only); split halves are 1080x960, crop ~1.125:1; a note at the seam must not cover the art's own bubbles.
