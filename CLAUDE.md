# comic-translator

Agent-driven comic acquisition, translation and localization into Vietnamese.

## Read before you...

The everyday rules are in this file. Longer, task-specific rules live in
`docs/` and are as binding as this file -- read the one that matches the task
**before** starting it:

- `comic ps ...` (Photoshop route, SFX, notes, watermarks, WebP publishing) ->
  `docs/photoshop.md`
- `comic video ...` or channel videos -> `docs/video.md`
- downloading, capturing or importing a chapter, or adding a site ->
  `docs/acquisition.md`
- changing detection / OCR / slicing code, a language new to the project, or
  OCR results that look wrong -> `docs/ocr-and-detection.md`
- setting up a new series (fonts, voices, margins) -> `docs/lettering.md`

## The one rule

**Claude/Codex is the only agent.** Everything in `comic/` is a deterministic local
tool: OCR, segmentation, inpainting, rasterization. There is no second LLM, no
sub-agent orchestrator, no autonomous planner, and the pipeline makes **no network
calls to any model API**. Where judgment is needed, the pipeline stops and asks the
agent — see the gate protocol below.

Supporting models (comic-text-detector, manga-ocr, RapidOCR, LaMa) are narrow tools.
They recognize, segment or paint. They never plan, never translate, never decide.

## Gate protocol

The pipeline runs deterministic stages and halts at three points where it refuses to
guess. A gate does three things and exits with code `10`:

1. Sets `chapter.json` to `status: awaiting_agent`, `pending_gate: <name>`.
2. Writes `workspace/<series>/<chapter>/handoff/<gate>_request.json`.
3. Writes the images the agent must look at into `handoff/<gate>/`, and prints the
   exact files to read and the exact file to write.

The agent reads those PNGs with its image-capable `Read` tool, writes
`handoff/<gate>_response.json`, then runs:

```
comic resume --series "<name>" --chapter <n>
```

Responses are validated against `comic/handoff/schemas.py` **before** anything is
applied. An invalid response reopens the gate with `gate_error` set, so a malformed
answer can never corrupt state.

| Gate | Opens when | Agent decides |
|---|---|---|
| `ocr_review` | OCR confidence low, or a heuristic smells wrong | transcribe the crop exactly, in the source language |
| `translate` | always | the Vietnamese line for each region, plus speaker, glossary and memory deltas |
| `visual_qa` | always, after rendering | whether each page reads correctly, and which fixes to apply |

### visual_qa uses a closed vocabulary

The agent decides *what is wrong*; the code decides *how to change pixels*. Valid
actions are only: `accept`, `font_delta`, `nudge`, `force_wrap`, `expand_mask`,
`shrink_mask`, `force_inpaint`, `retranslate_shorter`, `restyle`. Anything else is
rejected by validation. Do not add free-form pixel instructions to this gate.

## Pronouns are the series' memory, and they are pinned

Vietnamese has no neutral second person, so every line commits to a
relationship. A pair that drifts between chapters reads as a different
relationship, and readers notice that faster than almost any other error.

Only the agent can say which pair a line used -- the choice lives in the
wording, not in anything the pipeline can measure -- so the translate response
carries `address_pairs`, one entry per ordered (speaker, addressee), and
`translation/gate.py: merge_addresses` pins them into `profiles/<series>/`.
**An established pair wins over a newly reported one**; changing one is an
explicit profile edit, not something a later chapter does on its own.

The table is inherited by every later chapter, so it holds **named, recurring**
characters only. A one-off role -- "the culprit", "a bystander" -- must not go
in it: next chapter's culprit is a different person, and the entry would
otherwise persist forever.

## Front matter is not story content

**Do not translate the cover, the table of contents, credits or logos.** The
operator handles those. They are designed artwork, not lettering in bubbles:
the title is a drawn logo, the contents page is a typeset layout, and setting
Vietnamese into either destroys it while costing gate time.

Answer `""` for those regions at `ocr_review` so they are dropped, including
the ones the heuristics did **not** flag -- a confident reading like
`名侦探DETECTIVECONAN` sails through to the translate gate otherwise.

Such a page still belongs in `library/`, or the chapter reads with holes and
the page numbers stop matching the source. `qa/visual.py: publish` therefore
falls back to the original for a page with nothing translated, while still
reporting a page that *should* have rendered and did not.

Check before dropping: page 4 of this chapter holds two regions and looks like
front matter, but one of them is the splash line `她丈夫，也就是你！！`.

**What stays in English:** logos, the scanlator's notes, and in-world
signage (shop signs, a wreath's ribbons, a book cover). Phone messages, notes
shown to the reader and floating spoken lines carry the plot and are
translated, with the series' pinned pronouns.

## Agent memory lives in the project

Claude's memory for this project is `.claude/memory/` (index: `MEMORY.md`),
on E: with the project, so a Windows reinstall cannot take it. Claude Code
looks for it under the user profile, which is a **junction** to that folder.
After a reinstall the junction is gone and memory reads as empty -- recreate
it rather than starting over:

```
New-Item -ItemType Junction -Target "E:\Sefl_Study\tranlates_comic_project\.claude\memory" `
  -Path "$env:USERPROFILE\.claude\projects\e--Sefl-Study-tranlates-comic-project\memory"
```

(Delete the empty `memory` folder there first if Claude Code already made one.)

## Layout

```
library/<series>/Chapter_NNN/001.png   final, reading-ready pages ONLY
workspace/<series>/Chapter_NNN/        every intermediate artifact
profiles/<series>/                     durable knowledge new chapters inherit
```

Nothing but finished pages ever lands in `library/`. Source filenames are always
discarded and replaced with zero-padded `001.png`, `002.png`, … in true reading
order (`comic/paths.py: page_filename`).

## Invariants worth not breaking

- **Never regenerate a whole page.** Cleaning edits only masked regions; the
  artwork-preservation test asserts the cleaned page is pixel-identical to the
  original outside the dilated text masks.
- **SFX is left alone by default** (`UNTRANSLATED_KINDS` in `comic/schemas.py`).
- **Fonts are gated on Vietnamese coverage.** `comic/typesetting/coverage.py` rejects
  any face missing stacked diacritics. This is not theoretical: every handwriting
  face shipped with Windows fails it, and Bahnschrift fails by exactly one glyph
  (`ỷ`). A partially-covering font renders tofu silently.
- **Console must be UTF-8.** Call `setup_console()` in every entry point; the Windows
  default codepage raises `UnicodeEncodeError` on Vietnamese text.
- **State writes are atomic.** Use `comic/state/store.py: write_json`, never a plain
  `open().write()` — an interrupted write strands the chapter.
- **Respect site access rules.** Read only what a normal logged-in browser is served.
  No paywall, DRM or token-signing circumvention. Login-gated sources use the user's
  own persistent browser profile; locked chapters are refused, not worked around.

## Common commands

```
python scripts/bootstrap.py          # one-shot environment setup
comic doctor [-v]                    # what is missing, and the command that fixes it
comic status --series NAME           # per-stage, per-page progress
comic slice --series NAME -c N       # re-cut a webtoon strip, show the seams
comic analyze --series NAME -c N     # slice + detect + OCR, stops at ocr_review
comic review --series NAME -c N -r p010_r12   # look at a reading anyway
comic translate --series NAME -c N   # open the translate gate
comic render --series NAME -c N      # clean + typeset + centre/spill checks, stops at visual_qa
                                     # (mid-QA: only pages not already approved; --all = every page)
comic resume --series NAME --chapter N   # apply a gate response, carry on
pytest                               # unit + fixture tests, no network
python scripts/ocr_bench.py --series NAME --chapter N   # score OCR vs verified text
```

`comic slice` runs inside `comic analyze`; call it alone only to look at the
seams before paying for detection.

The pipeline's own publish writes PNG. Running `comic render`/`resume` again
on a chapter already published by `comic ps publish` (WebP) puts PNGs back
beside the WebP files -- see `docs/photoshop.md`.

For a source that is not bilibili, state what it is at import time:

```
comic import-raw --series NAME -c N --language ko --kind longstrip
```

`comic run` (whole pipeline in one command) and `comic rerun --page N --from
<stage>` are **not built yet**. Drive the stages individually for now; the
per-stage commands already take `--page/-p` to limit their scope.

## Cleaning: find the bubble, do not guess its shape

Two masks, and they are not the same thing. The **glyph mask** (dilated, so the
anti-aliased halo goes too) is what gets erased over artwork. The **bubble
interior** is what gets erased inside a bubble, and what the typesetter may
fill -- and it has to be the whole bubble, because Vietnamese runs longer than
the CJK it replaces and a line confined to the source's box has nowhere to go.

`bubble_interior` grows outward from the lettering instead of fitting a shape:
light pixels near the box, plus the glyphs (dark, or they punch holes in their
own bubble), then the connected piece the text sits in. The outline is dark so
it stops the fill by itself. Measured on 36 regions: every one recovered, at
1.1x-2.8x its text box, and it handles jagged shout bubbles and borderless
captions that no rectangle or ellipse fits.

Three traps, all hit here:

- **Vote with the region's own lettering.** The search window reaches past the
  box so a neighbour does not punch holes in this bubble -- which means it
  contains the neighbour's text. Counting all of it hands a small region its
  larger neighbour's bubble; measured, one region's interior covered *none* of
  its own box.
- **Two bubbles that touch are one component.** So are two captions on a shared
  white background. Measured: one interior covered 100% of its neighbour's
  lettering, and typesetting both would stack two lines in one place.
  `fit_areas` awards contested pixels to the nearest text box.
- **Floor every region at its own box.** Splitting a shared bubble by distance
  alone gave the smaller region 0.65x the room its own source text had, which
  cannot hold a longer translation.

**Cleaning erases the letters only, never the bubble** (changed 2026-10-09,
the operator's report on Super Lovers ch.0: "a white frame ... sometimes it
even has polka dots"). Filling the whole interior with one quantised colour
wiped dot screentone, gradients and colour shading, and never quite matched.
Now only the dilated glyphs change: filled with the exact median colour of a
thin band around them when that band is plain (`clean.plain_behind`), else
inpainted by LaMa. The Photoshop route's `--clean hybrid` hands those
textured glyphs to Content-Aware Fill instead. The rest of the bubble --
outline, tone, stray marks -- is the original's.

Three things followed, each measured on that chapter and tested:

- **Type over texture gets a halo** (`render.haloed`, 0.12 em of the light
  stroke colour): dark letters laid straight onto dot tone were hard to read.
- **A toned or screentoned box is found by a flood fill** over the blurred
  page in small steps (`mask.toned_interior`): the light-pixel search found a
  sliver of a grey dotted caption and set it at 13px. Windows widen 1.5x ->
  5x the text box, because a short line in a big box reaches a small
  window's edge.
- **Light lettering on a dark box takes the whole box** (`mask._inverted`).

Sizes: `source_size_cap` is also bounded by the text's density in its box --
merged line bands had lifted captions to the 64px ceiling; sentence case set
against a caps source is allowed 1.25x (its body is lowercase); and a page's
captions share one ceiling, their median (`render.layout_page`, used by both
renderers).

`clean_page` returns the union of every pixel it touched, so the
artwork-preservation guarantee is asserted rather than assumed. **Scope the QA
residue check to that same glyph mask**: measuring leftover ink over the whole
bubble counts the outline and neighbouring artwork, and reported 60-73%
"residue" on regions that had in fact been erased completely.

## Typesetting into a shape

**Read `docs/lettering.md` before setting up a new series.** It holds the
lettering rules (Piekos' *Essential Guide*, the editmanga rules) and which
setting carries each: faces per kind, `voices:` chosen per line at the
translate gate, `pad_em`, no-break spaces for words that must not split.

Fit by binary search over font size -- fitting is monotone, so the search is
exact. A line may only sit where its whole line-height band is solid, which is
what keeps text off a round bubble's corners.

**Wrap width and fit width must agree.** Wrapping to the bubble's widest point
and then placing the block fails, because in a round bubble only the middle row
is that wide: the search then collapses to a tiny font that fits anywhere.
Trying progressively narrower wraps (`WRAP_ATTEMPTS`) trades width for lines,
which is the trade a letterer makes by eye. Measured: minimum type on a page
went from 8px to 12px and overflows from 4 to 0.

Never break a Vietnamese word. A split word can orphan a diacritic onto its own
line, which reads as a different word or as none. `wrap` returns None instead,
which means "try a smaller font".

Draw through PIL, never OpenCV -- Hershey fonts have no Vietnamese at all and
would drop every diacritic silently.

### Room to spare is its own failure (measured over "Mi Imitador", 61 chapters)

A short line deep inside a big bubble fills the 48px search window, so the
leak guard used to reject it and the line fell back to its own text box: 183
of 2,591 regions were set at roughly half the source size. `bubble_interior`
now retries wider windows and accepts only an *enclosed* result. Giving those
lines the whole bubble exposed four more things, each fixed and tested:

- **Cap the size near the source's** (`render.py: source_size_cap`, 1.15x the
  lettering's own mask line height). The text box had been an accidental
  ceiling; without it "I LIKE YOU." came back at twice the source size.
- **Anchor the block to the source box** (`fit.py: fit_text(anchor=...)`).
  With slack everywhere, the fitter put text wherever the area was widest --
  including area that leaked through a *dashed* outline, which set a line
  above its own bubble. The anchored layout wins unless it costs over 15% of
  the size; forced breaks are anchored too.
- **No one-word last line** when a wrap without one fits at the same size.
- **Take the source's stray punctuation into the interior** -- but only
  specks on the region's own lines. Filling every small hole also erased a
  separate "..." elsewhere in the same bubble.

**Centre on the bubble, not on the source** (the operator, 2026-10-15:
"avoiding off-center text is crucial"). The anchored layout used to centre
each block on the scanlation's own text box, so wherever their English sat
off-centre ours did too; and the largest size that fits an egg-shaped balloon
often fits only high up. Now `fit._bubble_centre` measures the balloon along
a cross through the source box (rows narrower than half its widest are a
tail, and an axis measuring far past the box is a leak, so the source centre
is kept there), the block centres on that, and `fit._centred` gives up to
12% of the size to land on it. The free (unanchored) placement centres too.
`comic render` checks every page it renders for blocks still off-centre and
for lines spilling out of their bubble, and lists them in the visual_qa
request as `off_centre` / `spill` warnings (`comic/qa/placement.py`). Nothing
needs running by hand; `scripts/centre_check.py` and `scripts/spill_check.py`
print the same findings for a standalone re-check.

**Ink must read solid.** Augie's thin strokes at caption sizes on an ~850 px
scan anti-alias to grey ("some colours are too pale", 2026-10-15); the manga
preset sets captions and sentence-case lines in Felt-TB instead (full
coverage, no advance-width defect). Super Lovers keeps Augie for continuity.

**Margins are per series** (`fonts.yaml`: `pad_em`, `size_scale`, `inset`).
`PADDING_EM` only narrows each line, so a full bubble set its top and bottom
lines against the outline -- a reader called Mi Imitador's type "too big, it
touches the frame". `pad_em` keeps that many ems clear on **every** side
(Piekos, *Essential Guide to Comic Book Lettering*: the space inside a balloon
is about one capital O -- 0.6 em in AnimeAce3); `size_scale` multiplies the
source-size cap; `inset` is a margin as a fraction of the bubble instead.
**The shared `manga` and `manhwa` presets set `pad_em: 0.6` and
`size_scale: 0.9`**, so every series on a preset has the one-O margin
(Shuumatsu, Kimigazer, Itsuka, Super Lovers, Kiss Shite Sugar-kun, Dasadanan,
Golden Light). Text on open art, where no bubble was found, skips the one-O
margin and keeps only the side padding (`render.layout_region`). **Mi Imitador and conan use no preset on purpose**:
they keep the old side-only `PADDING_EM`, are finished and will not be
redone -- do not "fix" them. All go through `layout_region`, so the Photoshop
route follows.

**Coverage is not spacing.** 000Augie-TB (the manga preset's caption and
narration face) passed the coverage gate while 50 of its precomposed
Vietnamese letters carried advance widths up to 25% too wide -- every accented
letter was followed by a gap ("nhó c", "KÉ T"; Super Lovers ch.1,
2026-10-13). `scripts/fix_font_advances.py` repairs a face; the repaired Augie ships as
`000AugieVN-TB.ttf`, family "000 Augie VN [TeddyBear]" (original kept in
`assets/fonts_original/`). It needs **a new name**: overwriting the installed
file was not enough -- Photoshop kept serving the old widths from a font
cache even after a restart and after clearing its own CT Font Cache.

A face may lack only the punctuation in `coverage.SUBSTITUTES` (em/en dash,
guillemets) and still pass the gate; `render.printable` sets the stand-in.
Leading is one global 1.22 em: measure a new face's Ỗ-over-Ộ span before
using it multi-line -- Bangers spans 1.60 em and can collide.

Sealing dashed outlines morphologically was tried and abandoned: the detector
marks dashes near the text as glyphs, so the fill escapes regardless.

Recurring cases that stay manual at `visual_qa`: a bubble merged with an SFX
(inpaint so the outline survives, then `force_wrap`); white hand-lettering
over dark art (one ink colour cannot be read there -- drop it at
`ocr_review`, the original stays); a bubble detected as a caption
(`restyle` to dialogue). `font_delta` keeps `force_wrap` breaks.

## visual_qa: what the numbers can and cannot say

Pre-checks find overflow, type below the legibility floor, type far off the
page median, and leftover source ink. They cannot say whether a line sits badly
over a face, so they stay narrow and the page goes to the eye.

**`font_delta` upward is almost never the fix.** The fitter already chose the
largest size that fits, so raising it only overflows -- learned by doing it to
three regions and watching all three turn from warnings into errors. A bubble
flagged `tiny_font` is small; the remedies are `retranslate_shorter` (fewer
characters buys a larger size) or `accept`. The check now says so in its own
message.

**The centre and spill checks are pre-checks too** (`off_centre`, `spill`,
from `comic/qa/placement.py`; they used to be two scripts run by hand after
every render). They are warnings, not errors: dot-tone captions, SFX areas and
shared or leaky bubbles raise false alarms, so each is judged by eye. A region
can carry several findings; all of them reach the request.

Every round re-renders from the **original** with the stored `RenderPlan`, so a
fix can be reverted and the page comes back exactly as if it had never been
applied. Rounds are capped at `MAX_QA_ROUNDS`; past that the chapter escalates
rather than looping.

**Round 2 onward shows only pages that are not already approved.** A page
counts as approved when it was shown, the answer did not fix it or name it in
`blocked`, and none of its error-level pre-checks (overflow, leftover ink) went
unanswered -- an overlooked error comes back instead of vanishing. A render is
a pure function of its inputs, so each page shown is fingerprinted
(`qa.visual.page_fingerprints`: source, mask, analysis, its Vietnamese and
voices, its overrides, the series' fonts profile, the font files, the LaMa
model and the rendering code) and an approved page whose fingerprint still
matches would come out pixel-identical. `comic resume` re-renders everything
else -- fixed, blocked and error-carrying pages, pages never shown in this QA
cycle, pages whose translation or profile changed -- and the request names the
rest in `unchanged_pages`: approved, not re-rendered, not to be read again.
The whole page is the unit, since captions on a page share one size ceiling.
`passed=true` publishes every page and clears the approvals, so the next
render of the chapter starts a fresh cycle that shows everything. An answer of
accepts only re-renders and shows the whole chapter, so the gate is never
empty. The gate folder holds only this round's pages.

Safeguards, each from a review that broke an earlier version: a page is
skipped only when its inputs *and* the rendered file on disk still match the
approval (a trial render that was later reverted does not pass); the inputs
are fingerprinted before a render starts, so an edit made during it is not
mistaken for shown; a fix naming an unknown region is refused like the
translate gate refuses one; `blocked` entries count every page they name
("pages 3 and 4", "pages 3-5"); the answer file is renamed to
`visual_qa_response.applied.json` once used, so a second `comic resume`
cannot apply it again; and `passed=true` publishes nothing while any page has
not been shown as it now stands -- those pages are rendered and shown first.

`retranslate_shorter` reopens the *translate* gate for those regions, because
shortening a line is a translation decision, not a rendering one. The
approvals stay in `render_plan.json`, so the next `comic render` renders only
the pages that are not approved (the retranslated ones included, since their
Vietnamese changed); `comic render --all` renders every page regardless.

Only rendered pages are published, and a chapter published incomplete is marked
`partial` -- a half chapter in `library/` would otherwise look finished.

