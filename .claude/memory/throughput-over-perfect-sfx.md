---
name: throughput-over-perfect-sfx
description: User 2026-10-15 -- translate many chapters; hard SFX get a small corner note instead of in-place replacement; translate only 50-70% of SFX; dialogue centred is the priority; no pale ink
metadata:
  type: feedback
---

The user (2026-10-15, clarified the same day): do not spend a chapter's worth of time and tokens chasing perfect SFX. Translate dialogue first and keep moving through chapters.

**SFX rule:** no blanket rule -- judge per chapter (user, 2026-10-15). Full-colour comics (webtoons/manhwa/manhua) usually have every SFX translated; black-and-white manga far less often. Decide from the chapter itself how much SFX work it calls for; the points below are the default for a B&W manga chapter.
- **Easy SFX: 100% replaced in place, always** (user, 2026-10-15: "for the easy ones, you still need to complete 100%").
- "Difficult" SFX (hard to erase/edit, hard to translate, or hard even to identify) are NOT replaced. Leave the original art alone and add a **small translator's note** (e.g. `Rắc rắc`).
- **Where the note goes (user, 2026-10-15, with examples):** NOT right next to the SFX -- that covers art. Put it in empty space: a gutter between panels (vertical strip -> rotated text), the page margin, a blank corner of the panel. "Pick the right angle for placement." `comic ps build` finds such a spot automatically (`photoshop/notes.py`) when a note item has no bbox.
- The 50-70% allowance is **only for the difficult ones, and only on a page crowded with them** -- not a quota for the chapter's SFX overall.
- Merged or awkward dialogue items that are hard may also be noted in `workspace/<series>/sfx_todo.md`.

**Priority order:** (1) every dialogue line centred in its own balloon -- off-centre text is what the user notices most; (2) readable ink -- dark letters solid, no thin/pale faces ("some colours you used are too pale" -> manga captions moved to Felt-TB); (3) SFX as above.

**Why:** with a Pro plan, one chapter per session is too slow; most of the value is in the dialogue.

**How to apply:** per chapter: translate -> render -> check centring (`scripts/centre_check.py`) -> SFX pass (every easy one replaced; difficult ones as notes in empty space, 50-70% of them where a page is crowded with them) -> build -> leftover_scan -> publish, then next chapter. Related: [[super-lovers-finish]], [[mistakes-log]], [[work-autonomously]].
