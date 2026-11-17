---
name: translate-interjections
description: "2026-11-09/10 -- translate interjections (huh?, ah!, ha) AND groans/strain sounds (UGH/UHG, NGHH) always; crying WORDS (SOB, SNIFF) too; only bare HIC/HUU noises and ?/! stay as drawn"
metadata:
  node_type: memory
  type: feedback
  originSessionId: 7f16aa04-d4fc-4258-98e8-be067451a088
  modified: 2026-11-10T20:40:00.000Z
---

Translate words like "huh?" (HẢ?), "ah!" (A!), "ha", "hmm" -- in bubbles and as SFX. **Crying written as an English
word is translated too** (user 2026-11-10, on MID 28 "SOB" left out: "it should have been translated"): SOB -> HỨC HỨC /
NỨC NỞ, SNIFF -> SỤT SỊT, WAAH -> OA OA. Only bare crying noises that read the same in Vietnamese (HIC, HUU, UUU) and
bare ?/! stay. An SFX left untranslated (e.g. too tangled with a character to erase) stays AS DRAWN -- never erased
without a replacement ("don't just remove it").

**Groans and strain sounds are always translated** (user, 2026-11-10: an amendment to the guidelines, "so it is
easier to understand"), chosen by what the panel shows, any spelling:
- UGH / UHG / UGHH: annoyed -> HỪ / TRỜI Ạ; pain, effort, hit -> Ư… / HỰ; disgust -> Ọẹ / Ẹ. Inside a sentence,
  translated with the line. (The old prompt's "UGH -> ỰC" was wrong: ỰC is a gulp/choke.)
- NGHH / NGH / NNGH: straining, pain held in, suppressed moan -> HỰ / Ư Ư / ƯM.

**Why:** the user, 2026-11-09 -- replaces the older "leave near-meaningless noises (HA, AH, HUH)" policy; 2026-11-10
added the groans explicitly.
**How to apply:** in my own translate answers and in the AgentRouter prompt (`comic/agents/prompts/translate.md` rule 6,
gate.py instructions, docs/photoshop.md policy -- all updated 2026-11-10). `comic translate --missing` on a chapter picks
up interjections left out earlier. See [[throughput-over-perfect-sfx]].
