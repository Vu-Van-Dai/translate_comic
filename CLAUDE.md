# comic-translator

Agent-driven comic acquisition, translation and localization into Vietnamese.

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
comic render --series NAME -c N      # clean + typeset, stops at visual_qa
comic resume --series NAME --chapter N   # apply a gate response, carry on
pytest                               # unit + fixture tests, no network
python scripts/ocr_bench.py --series NAME --chapter N   # score OCR vs verified text
```

`comic slice` runs inside `comic analyze`; call it alone only to look at the
seams before paying for detection.

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
`scripts/centre_check.py` lists blocks still off-centre; run it per chapter
alongside `spill_check.py`.

**Ink must read solid.** Augie's thin strokes at caption sizes on an ~850 px
scan anti-alias to grey ("some colours are too pale", 2026-10-15); the manga
preset sets captions and sentence-case lines in Felt-TB instead (full
coverage, no advance-width defect). Super Lovers keeps Augie for continuity.

**Margins are per series** (`fonts.yaml`: `pad_em`, `size_scale`, `inset`,
all off by default). `PADDING_EM` only narrows each line, so a full bubble
set its top and bottom lines against the outline -- a reader called Mi
Imitador's type "too big, it touches the frame". `pad_em` keeps that many
ems clear on **every** side (Piekos, *Essential Guide to Comic Book
Lettering*: the space inside a balloon is about one capital O -- 0.6 em in
AnimeAce3); `size_scale` multiplies the source-size cap; `inset` is a margin
as a fraction of the bubble instead. Golden Light uses pad 0.6 / scale 0.9.
All go through `layout_region`, so the Photoshop route follows.

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

## Photoshop route (`comic ps`) -- separate from the pipeline

A second output for a chapter that has already been rendered: per page, a
layered PSD with **editable** Vietnamese text (group "Vietnamese text" over
"Cleaned", with the hidden "Original" at the bottom) plus a PNG preview, in
`workspace/<series>/<chapter>/photoshop/`. It reads the pipeline's artifacts
and never writes to them or to `library/`.

```
comic ps check                       # COM reachable? which fonts can Photoshop see?
comic ps fonts --series NAME         # per-user font install; restart Photoshop after
comic ps build --series NAME -c N [--clean pipeline|hybrid|content-aware]
```

- **It makes no typesetting decisions of its own.** `photoshop/plan.py` calls
  the same `fit_areas` / `layout_region` / `source_size_cap` as
  `render_page`, and a test pins the two together. Only the medium changes.
- **Fonts live in `C:\Program Files\Common Files\Adobe\Fonts`** (made
  2026-10-13, all 23 project faces copied in, admin rights needed once).
  Photoshop loads that folder at startup without a Windows install. A
  per-user Windows install added mid-session was *never* seen by Photoshop
  -- not after restarts, `AddFontResource` or clearing CT Font Cache. Copy
  any new or repaired face there (elevated), then restart Photoshop.
- **Photoshop sees installed fonts only, read at startup**, addressed by
  PostScript name. A missing face silently falls back, so `build` refuses to
  run until every face is visible.
- **One `DoJavaScript` per page.** The page is built by one JSX script with
  its plan inlined as a JS literal -- per-property COM calls are slow, and
  ExtendScript has no `JSON` object.
- **Opening a file twice returns the same document.** Closing a "second"
  copy closes the page being built; duplicate the layer in place instead.
- **Content-Aware Fill is a trade, not an upgrade** (measured on ch.54):
  `hybrid` removed the pipeline's smear across a bubble edge but redrew the
  outline through a word, and left a smudge elsewhere. `pipeline` is the
  default; the PSD exists so an operator can finish such spots by hand.
- PSD text matches the PIL render to within glyph anti-aliasing; leading is
  set from PIL's first and last baselines so multi-line blocks do not drift.

### Publishing the Photoshop route, as WebP

`comic ps publish --series NAME -c N` replaces **every** page of the chapter
in `library/` -- the Photoshop PNG where one exists, the original for a page
with nothing translated -- and removes each page's previous file, so a page
never exists in two formats. It refuses if any translated page has no
Photoshop build: half a chapter from each route would look finished.

Default output is **WebP quality 95**. Measured over 24 pages of this
series:

| setting | size vs PNG | worst PSNR | worst SSIM |
|---|---|---|---|
| lossless | 42% | -- | 1.0000 |
| **q95** | **14.6%** | **47.5 dB** | **0.9953** |
| q90 | 10.3% | 44.7 dB | 0.9913 |

No difference is visible at q95 even in fine coloured hatching at 3x zoom,
which is where WebP's half-resolution chroma would show first. Ch.46 went
30.1 MB -> 4.5 MB.

**Caveat:** the pipeline's own `publish` still writes PNG. Running
`comic render`/`resume` again on a chapter published from this route would
put PNGs back beside the WebP files.

### Sound effects (Photoshop route only)

`comic ps sfx` crops every region the review dropped; the agent writes
`photoshop/sfx_response.json` naming only the SFX to replace; `comic ps build`
erases those with Content-Aware Fill and sets the replacement in a group
"SFX". The pipeline itself still never touches SFX.

- **Difficult SFX get a note, not a fight** (the operator, 2026-10-15): an
  SFX that is hard to erase, translate or even read is left as drawn, and an
  item with `kind: note`, `text` sets
  a small translator's note in empty space -- a gutter, the page margin, a
  blank panel corner, never on the art beside the SFX (`photoshop/notes.py`
  finds the spot when the item has no `bbox`; a tall gutter takes it turned).
  Every *easy* SFX is still replaced; only on a page crowded with difficult
  ones is 50-70% of those enough.
- **Policy (the operator's):** translate every SFX that means something,
  short or long (STEP, HUG, BEEP, SLAM, PANT, TRUDGE, FLINCH); leave only
  near-meaningless noises (HAHA, HUH, HIC, AH, HAA, FUU) and bare `?`/`!`.
  CJK SFX go to English first, Vietnamese only if that English is hard. Match the original's colour, outline and angle --
  an intended exception to the dialogue's one-ink rule.
- **Dropped regions are re-derived from the mask**, which reproduces the
  review's ids and boxes exactly (174 of 174 kept regions over three
  chapters), so a dropped id means the same lettering.
- **Ink and outline are read from structure, and ideally declared.** Every
  colour-statistics rule broke on real pages: a median over the fat mask
  turned black letters grey; a light hand in the ring flipped SQUEEZE; black
  letters on a black panel merged into it (THROB); the grey anti-alias edge
  passed for an outline (DRAG). Now the darkest and lightest colours are the
  candidates, anything joined to the zone edge is background, and the
  thicker of the two inside the mask is the ink -- right on 12 of 15 checked
  words. The 3 misses are all the same case: on a dark panel the white
  outline is drawn *thicker* than the black strokes. Since every SFX in this
  series (89 of 89) is black with a white outline, the profile says so:
  `fonts.yaml: sfx: {ink: dark}`. Other series default to `auto`.
- **When the detector's region is wrong for a word, give the item a `bbox`.**
  It may cover part of a word (FLINCH: only the H; YAWN: not the Y) or merge
  in marks beside it (DRAG: the "???"). `"letters": "colour"` finds the
  letters inside the box by colour; `"mask"` clips the detector mask to it.
- **Erase the letters plus their halo** (`SFX_HALO`), clipped to the box: a
  light outline left in the hole is smeared into a pale blob by every fill.
- **`"fill": "lama"`** erases one SFX with the local LaMa model instead of
  Content-Aware Fill, which samples the whole page and can copy a nearby
  line in (DRAG grew a dark rule; TAP smeared over a jaw).
- Pages are RGB from `load_page`; reading them as BGR swapped red and blue in
  SFX colours. Invisible on this series' black-and-white SFX, fixed anyway.
- **Fit the width, not the box.** Stacked diacritics make the Vietnamese
  taller than the original's capitals; fitting both sides set it at half size.
- Content-Aware Fill has been enough so far. Generative Fill is allowed by
  the operator where it is genuinely needed, but it uploads the page to
  Adobe's servers, so it stays off unless a page calls for it.
- **Size to one letter, not the word's box.** A staggered word (CHIRP
  stepping down) has a box twice a letter's height, and fitting to it set
  CHÍP at twice the original. `letter_height` is the median ink-blob height;
  Photoshop fits the word *without its diacritics* to it, so accents stand
  above the original's capital height as they would over any capital.
- More per-item controls, each from a real miss over ch.0-59: `parts`
  (a word the detector split -- erase every piece, nothing between them),
  `letters: whole` (take the rest of a letter the mask only grazed; a blob
  that runs off the search window is a panel, not a letter), `poly`
  (lettering on a slant beside more lettering -- phone messages), `halo`
  (motion-blurred letters), `angle` (staircase words measure as steep
  diagonals), `scale`/`offset`, `ink`/`colour` (a word that breaks the
  series rule, a gradient fill) and `font` (a spoken line lettered onto the
  art, set in the dialogue face).
- **Review dropped real lettering too**, not only SFX: bubbles, captions,
  half of a line the detector split into words (ch.0 "Cái IN gì- WORLD").
  An item with `kind: dialogue|caption|...` is set exactly as the pipeline
  sets that kind; `replaces` lists kept fragments it supersedes.
- **Other groups' watermarks and credit tags on story pages are erased**
  (the operator, 2026-10-09): an item with `erase_only: true` removes the
  lettering and sets nothing. Replace with "Raichi" (our watermark) or
  "Rồng nhỏ" (our group, for another group's logo) only when the operator
  asks; erasing completely is the preferred outcome (re-confirmed
  2026-10-11). Examples: a scanlator's tag at a page edge ("SWEET PURINSU
  SCANS | TUMBLR"), a translucent site block over the art ("WEB CHÍNH CHỦ:
  MEOMEOTEAM.SITE ..."). Detection misses most watermarks -- OCR the whole
  page for group names and site URLs before building. Whole credit pages
  are the operator's call.
- **What stays in English:** logos, the scanlator's notes, and
  in-world signage (shop signs, a wreath's ribbons, a book cover). Phone
  messages, notes shown to the reader and floating spoken lines carry the
  plot and are translated, with the series' pinned pronouns.
- **Lettering the detector never marked** (a scanlator's handwritten aside,
  the "AY?" of an "OKAY?" it cut short) has no mask to erase. Give the item
  a `bbox` with `letters: colour` -- for a `kind:` item too: the letters found
  by colour join the mask, so they are erased and fitted like detected text.
- Photoshop addresses a font whose PostScript name has spaces by the name
  with hyphens (`000-Clairvoyant-BB-[TeddyBear]`); `fonts.font_info` does
  the same, or `build` reports the face missing although it is installed.
- A page split mid-word between two source slices (ch.1, never re-cut)
  cannot take one replacement; such a word stays as drawn.
- `plan.py: refill_flat` is retired: it corrected the old whole-bubble fill,
  which no longer exists, and matching pixels by that colour could recolour
  art.

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

Every round re-renders from the **original** with the stored `RenderPlan`, so a
fix can be reverted and the page comes back exactly as if it had never been
applied. Rounds are capped at `MAX_QA_ROUNDS`; past that the chapter escalates
rather than looping.

`retranslate_shorter` reopens the *translate* gate for those regions, because
shortening a line is a translation decision, not a rendering one.

Only rendered pages are published, and a chapter published incomplete is marked
`partial` -- a half chapter in `library/` would otherwise look finished.

## Access-gated sites (measured 2026-09-20, yaoiscan re-measured 2026-09-21)

| Host | Plain automation | Route |
|---|---|---|
| comix.to | 200, pages load | direct, but lazy-loads — see note |
| manga.bilibili.com | 200 | needs the operator's login |
| theblank.net | 403 Cloudflare interstitial | `--chrome`, plus `comic capture` |
| yaoiscan.com | 403 headless; clears itself in a real Chrome | `--chrome`, **proven** |

A bot challenge is a **terminal outcome**, raised as `BotChallengeError`. Do not
add stealth patches, fingerprint spoofing or CAPTCHA solving to get past one —
that is circumventing an access control and is out of scope for this project.
The supported route is `comic chrome` + `comic download --chrome`, which reads
through a browser the operator signed into and the site has already cleared.

**That route is now proven, not theoretical.** On yaoiscan the interstitial
(localized: `Chờ một chút...`) cleared by itself within three seconds of a real
Chrome loading the page -- no interaction, no patching -- and `download
--chrome` then pulled a chapter: 6 files, 720px wide, each ~17000px tall,
97325px of strip in total. So the 403 in the row above is what *headless*
automation sees, not a wall.

When attached over CDP the browser is **borrowed, not owned**: `close()` must
disconnect without closing it, or it will shut the operator's windows.

Repeated automated hits get throttled. comix.to served 3 images on one visit and
an empty document shortly after. Prefer the saved fixtures in `tests/fixtures/`
over re-probing a live host.

## What theblank.net actually does (measured)

Pages are **not** images. The chapter is ~89 `<canvas>` elements; the only
`<img>` tags are the site logo. Page data arrives from
`/p/<token>/1/<key>/<n>-1080.ece` as `application/octet-stream` with **no image
magic bytes at any offset** -- it is encrypted and decoded in JS before being
painted. Every image visible on the network is an ad CDN.

So: no decryption, and no `toDataURL` on those canvases. Both defeat a content
protection measure. `comic capture` screenshots rendered elements instead.

The reader also **virtualises**: only canvases near the viewport are painted.
Screenshotting the rest returns a uniform placeholder (measured: exactly 2
distinct colours, ~6 KB, vs 3000-8000 colours for real artwork). `capture.py`
therefore waits for paint and re-checks, and counts what never painted --
otherwise a chapter of blank pages looks like a success.

Known-unfixed: `Emulation.setDeviceMetricsOverride` does not raise screenshot
resolution on a CDP-attached Chrome, so captures come back at CSS size (901px
wide) rather than the source's 1080px. Worth less than it sounds -- OCR
accuracy saturates well below that, see "Resolution saturates around 500px".

## A webtoon is one strip, not a stack of pages

A long-strip source does not serve pages. It serves an arbitrary stack of tall
slices whose boundaries are an artifact of the uploader's export step --
routinely a fixed 1500px -- landing wherever they land, including through the
middle of a bubble. Two slices are not two pages, and treating them as such
puts half a bubble on each.

So `vision/strip.py` concatenates the chapter into one logical strip and
re-cuts it at rows that are flat all the way across. That is a guarantee, not
a heuristic: lettering contrasts with whatever it sits on, so a row holding any
part of a glyph cannot pass the test.

Measured over 10956 rows of real source pages, a solid row's spread is
**exactly 0** (1404 rows), the 20th percentile is already 239, and only 54 rows
in the whole sample land between 7 and 16. Rows are flat or they are artwork,
with almost nothing between, so `FLAT_SPREAD = 6`. It was 16 first, and at 16 a
softly blurred panel measuring 13-22 was chosen as a seam.

Four traps, all hit here:

- **Do not decide "is this a strip?" by height.** The usual export step is
  *shorter* than a page, so every file passes a height test while half the
  joins run through artwork -- which is exactly the case slicing exists for.
  Ask what the existing joins look like instead: a file that ends where a page
  ends, ends in gutter. `needs_slicing` re-cuts only when most joins do not.
- **A chapter has more than one gutter colour.** Assuming one was a real
  defect, found on the first live webtoon: yaoiscan ch.17 letterboxes on
  **black** for its opening scene and white thereafter. Its flat rows sat at
  255 (39%), 254 (34%, the same white shifted one step by JPEG) and **0
  (24%)**. A single modal vote picked white, discarded all 4911 black gutter
  rows as artwork, and forced **20 of 53** seams into blind cuts.
  `gutter_levels` therefore takes peaks greedily and accepts every level
  holding `MIN_GUTTER_SHARE` of the flat rows: 20 forced became 8.
- **A forced cut goes at the quietest row, not the target.** Same chapter:
  cutting at the target height put *every* forced seam through
  maximum-contrast content -- median row spread **255**, max 255, none below
  16. Taking the minimum-spread row in the window instead gives median **29**.
  It costs two extra forced seams to cascade and is worth it by a mile.
- **Never hold the strip in memory.** Measured on the first live webtoon:
  720 x 97325 px, which is **210 MB** decoded in colour, from six source
  files. Seam-finding needs one number per row, so slices are measured one at
  a time and re-read when pages are emitted.

A page with no flat band in range is still cut -- at the quietest row in
range, per the bullet above -- with the seam recorded as `band=0` and
reported. Refusing would leave a 60000px "page" no later stage can handle;
cutting silently would hide that a bubble may have been split.

`original/` stays the verbatim archive, so re-cut pages go to `pages/` and
`paths.source_pages` picks whichever exists. A paged source copies nothing.

Slicing runs at the top of `comic analyze` and rewrites `state.pages`
wholesale, because after a re-cut the old page numbers name files that no
longer exist. It is gated on `page_kind: longstrip` from the adapter -- *not*
on the measurement alone, because a full-bleed paged manga would also show
non-flat joins and concatenating one would be a disaster.

## Korean: use the recogniser, not the detector (measured)

RapidOCR ships Chinese weights only. Korean needs `korean_PP-OCRv5_rec.onnx`
plus the 11945-entry charset, which the official export keeps in its
`inference.yml` rather than in ONNX metadata -- `fetch_models.py` rewrites it
into the keys file RapidOCR wants.

The older `korean_mobile_v2.0` model is also available and is not worth using:
0.345 char similarity against v5's 0.581 on the same lines, and its charset
holds 1758 Hangul syllables against v5's 11172 (the complete precomposed set).

**The Chinese detector must not run on Korean.** It is not merely unhelpful,
it is destructive -- measured on clean rendered lines:

| crop | detect + recognise | recognise only |
|---|---|---|
| 24px | 0.683 / 4 of 12 | **0.976 / 9 of 12** |
| 32px | 0.538 / 3 of 12 | **0.969 / 8 of 12** |
| 44px | 0.517 / 3 of 12 | **0.969 / 8 of 12** |
| 60px | 0.529 / 1 of 12 | **0.988 / 11 of 12** |

It splits syllables apart and appends fragments: `정말 미안해요` came back as
`름|안해요성들` through the detector and perfectly without it.

Running it was redundant anyway. comic-text-detector has already decided where
the region is, so the crop *is* the text; all that remains is cutting it into
the single lines a recogniser expects, which `ocr/registry.py: line_bands`
does by projection. On five multi-line bubbles it found the right line count
5 of 5, at 0.970 char similarity -- against 0.663 for the proven Chinese path.

The margin that rests on is larger than it looks: measured over eight lines at
five sizes from 18px to 64px, the longest clear run *inside* a line of Hangul
is **0 rows**, because across a row of syllables something always has ink. So
a line breaks only at a genuine inter-line gap.

**Contrast Chinese, where the detector must stay.** It is what finds vertical
columns and rotates them, which is the only reason stacked CJK reads at all.
Recognition-only is a Korean-specific choice, not a general one.

**Hand `line_bands` the mask, not just the crop.** Thresholding the crop
counts every dark pixel, and the darkest thing in a bubble is the bubble's own
outline: it added a thin band above and below every bubble, which the
recogniser read as punctuation. Measured end to end on a rendered Korean
chapter, projecting the mask instead took char similarity from **0.909 to
0.985**, exact matches from 3 of 10 to 7 of 10, and mean confidence from 0.83
to 0.97. `read_region` therefore takes a `mask`, and resizes it with the crop
-- a mask that no longer lines up with the pixels it describes is worse than
no mask.

Segment ink by the minority class, not by darkness: inverted panels are
ordinary, and assuming dark ink finds bands in the gaps and reads the spaces.

**Caveat: the numbers above are on *cleanly rendered* Malgun Gothic in plain
white bubbles**, not on real webtoon lettering, which is stylised, tightly set
and often over artwork. Chinese scored 1.000 on equivalently clean material
and 0.608 on the real thing. So 0.985 is evidence the engine and the wiring
are sound, not the accuracy a real chapter will show. A true figure needs
ground truth, so it only exists after a chapter has been through the gate --
`scripts/ocr_bench.py` then scores it.

### That chapter was not Korean -- how the measurement went wrong

A first attempt to measure Korean on real material produced 0.761 mean
confidence and a 37% gate load. **Both numbers were meaningless**, and the
reason is worth more than the numbers would have been.

yaoiscan publishes English **scanlations** of Korean manhwa: the bubbles are
relettered in English, the SFX are left as stylised Hangul brushwork, and the
chapter title stays a drawn Korean logo. The adapter said `source_language:
ko`, so a Korean recogniser was aimed at English lettering and reported it
without complaint.

Note where the existing guard failed. `provider_for` refuses to *substitute* a
wrong-language engine and it behaved correctly -- it was told `ko` and loaded
Korean. The error was upstream in the **adapter**, which nothing checked.
`ocr/registry.py: language_mismatch` now compares the script actually read
against the one the declared language implies, and warns when
`MISMATCH_SHARE` of readings disagree. Verified on this chapter: declared
`ko`, it reports "100% of readings are latin". One English sign in a Korean
comic will not trip it; a whole chapter will.

Re-read as English, the same 152 regions give **0.904 mean confidence, 63% at
0.95 or above** -- so the pipeline handles Latin scanlation lettering well.
**Korean remains unmeasured on real Korean material**; only the 0.985 synthetic
figure exists. Getting a real one needs a raw Korean source, not a scanlation.

### The detector over-triggers on webtoon artwork

Reviewing the 56 flagged crops by eye, **24 of them held no lettering at all**:
10 ornamental ironwork and display-glyph fragments, 7 pieces of the chapter
title logo, 4 high-contrast effect strokes, 3 Korean SFX. That is 43% of the
gate load spent on artwork, which is a *detection* cost, not an OCR one, and
it does not appear on paged manga.

It also exposes a limitation of the Korean provider. Because it deliberately
skips PP-OCR's detector (see above, that detector wrecks Korean), it has no way
to say *there is no text here* -- it segments whatever ink it is given and
recognises it. Measured on page 4, eleven regions of pure ornament: the English
path, which does run a detector, returned empty for **10 of 11**; the Korean
path invented readings for about half, one at 0.69 confidence. So on a source
with heavy decorative art, expect Korean to hallucinate where Chinese and
English decline. The gate catches these, but they cost review time.

## Japanese is the one language that needs no help (measured)

manga-ocr on rendered Japanese, against a hand-written ground truth:

| shape | char similarity | exact |
|---|---|---|
| vertical column | **1.000** | 8/8 |
| horizontal line | **1.000** | 8/8 |
| two-column bubble, right to left | correct | -- |

Note the third row. For Chinese, `_in_reading_order` exists precisely because
RapidOCR hands columns back in the wrong order; manga-ocr orders a multi-column
bubble correctly by itself, so none of that machinery applies to Japanese.

This corrects an easy misreading of the number recorded elsewhere here:
manga-ocr scored 0.62 on vertical **Chinese**, which was never its job. On
Japanese it is the strongest engine in the project.

## Detection notes (measured on a real manga page)

The detector's **segmentation mask is reliable; its box proposals are not** --
4 boxes vs 15 real text regions on the same page. Regions are therefore derived
from the mask (dilate to group glyphs, then connected components), with boxes
used only as a hint. Reversing that loses whole speech bubbles silently.

Thresholds in `vision/regions.py` are measured, not guessed:

- text density: real lettering 0.31-0.42, mask noise over artwork 0.03-0.06
- bubble background: bubbles luma 235-251 / spread 10-51; text over artwork
  luma 137-217 / spread 74-105

Sample the background from non-text pixels *inside* the box, not a ring outside
it -- a tight box sits against the bubble outline and reads as artwork.

## OCR returns runs, not sentences (measured on 37 regions)

RapidOCR's recogniser is horizontal-only, but its detector finds vertical
columns and rotates them, so it reads CJK set in columns **perfectly** -- and
hands the columns back in roughly top-to-bottom order, which for text that runs
right to left is backwards. Joining its output in engine order therefore
produced `全部实情了生早已吐出你的主治医死心吧` where the page says
`死心吧你的主治医生早已吐出全部实情了`.

This is the dangerous kind of wrong: every run scores 0.96-1.00, so nothing
looks suspicious, the region never reaches `ocr_review`, and the agent is asked
to translate a shuffled sentence. Order the runs by their boxes
(`ocr/registry.py: _in_reading_order`); never `"".join` raw engine output.

Orientation must be decided from **the detected runs, not the region**. The
region's aspect ratio is measurably wrong: a bubble holding four short columns
is 93x78 -- wider than tall -- so `h > w * 1.6` calls it horizontal and the
columns come back reversed. A strict majority of runs taller than
`COLUMN_ASPECT` times their width decided all 37 regions correctly, including
the mixed ones; the region shape is only a tiebreak. Reversing unconditionally
is not an option either -- it scrambles horizontal captions like
`毛利兰 / 帝丹高中二年级`.

Group runs into a column (or a line) before ordering within it -- but group by
measured **overlap**, not by a distance tolerance. A tolerance scaled to the box
size is about one column wide, so it merges *neighbouring* columns and then
orders them top-to-bottom, reversing the sentence. Runs the detector split
inside one column overlap 0.9+; adjacent columns reach 0.1-0.5 even when the
boxes are drawn loosely.

### Confidence does not detect a truncated reading

The worst failure here is not a low score. On a bubble reading
`你就跟那些写信给你的女孩子去玩吧！` the engine found **one of three columns**,
returned `子去玩吧`, and scored it **1.00** -- because what it did read, it read
correctly. Nothing downstream can tell that two thirds of the line is missing.

So compare the reading against the detector's own mask:
`ocr/registry.py: unread_mask_fraction` measures the share of mask text pixels
that no OCR run covered, and anything over `MAX_UNREAD_MASK` goes to
`ocr_review`. Measured over 36 regions this ranked them almost exactly by how
much they lost -- everything at 0.22 and up was genuinely truncated (even the
single glyph `哇`, which had dropped its `!!`), everything at 0.08 and below was
complete. The mask is dilated, so the floor is not zero.

The threshold is set low deliberately: a false stop costs one crop read, a miss
corrupts a line. And say *why* in the gate request -- a truncated region arrives
at full confidence, so reporting "low confidence (1.00)" sends the agent looking
for the wrong problem.

### Do not tune the detector thresholds; it has been measured

Every reading corrected at `ocr_review` is ground truth, which makes a reviewed
chapter a free evaluation set -- `scripts/ocr_bench.py` scores against it.
Twelve configurations were swept over 37 verified regions:

| config | char similarity | exact |
|---|---|---|
| **baseline** | 0.663 | **14/37** |
| `box_thresh` 0.2 / 0.3 | 0.673 | 14/37 |
| `box_thresh` .2 + `unclip` 2.0 + upscale | 0.686 | 11/37 |
| `unclip_ratio` 2.0 | 0.660 | 12/37 |
| `limit_side_len` 1280 | 0.562 | 4/37 |

Nothing beats the baseline. Every setting that nudges character similarity up
*lowers* exact matches -- it is adding partial fragments, not recovering words,
and a half-right line is worth less than a clearly-wrong one because it looks
plausible. Raising `limit_side_len` is sharply worse.

**Those numbers are a 37-region subset; do not read them as the chapter.**
That was every region reviewed at the time. Once all 404 had been through the
gate, the same command over the whole chapter reports **0.608 mean, 68 exact
(17%)** -- lower because the full set includes the regions where the engine
returned *nothing* and the transcription came from the eye, which score 0.
Re-measure before concluding a change regressed anything: the sample grew, the
engine did not get worse. (Verified directly for the `mask` argument added for
Korean -- over 34 Chinese regions, readings with and without it were identical,
because `RapidOcrProvider` ignores it.)

Note `limit_type: min`, `limit_side_len: 736`: RapidOCR **already** scales every
crop so its shortest side is at least 736px, about 9.8x for a typical bubble.
That is why upscaling a crop before handing it over changes nothing, and why
pushing the internal size further only blurs more.

### Resolution saturates around 500px; we are past the knee

The obvious story -- "captures are 590px against an 1100px source, so accuracy
is resolution-bound" -- **is not what the numbers say.** Scoring the same 37
verified regions with the page progressively downscaled:

| page width | char similarity | exact |
|---|---|---|
| 590 (current) | 0.668 | 13/37 |
| 501 | 0.675 | 13/37 |
| 413 | 0.666 | 8/37 |
| 324 | 0.411 | 3/37 |
| 236 | 0.071 | 1/37 |

Accuracy falls off a cliff below ~400px and is flat above ~500px. We sit on the
plateau, so going to 1100px is extrapolating past a saturation point, not
climbing a slope. What limits the readings is the engine on this material --
stylised vertical Chinese manga lettering in tight bubbles -- not pixel count.

Caveat, because it matters before anyone redoes acquisition on this basis:
downscaling a 590px capture is not the same as truly capturing at 1100px, which
would contain detail this test cannot synthesise. The curve is evidence that
the gain would be modest, not proof that it would be zero.

Practical consequence: **budget for the review gate rather than chasing
resolution.** Around 60% of regions need an eye on this source, and that is
inherent, not a defect to engineer away.

### Upscaling a crop does not lift it either

At 590x913 these glyphs are ~12px. Enlarging the crop 2x-5x before OCR recovers
almost nothing (measured: a 5-column bubble read 5 runs at every scale, with the
same characters wrong), because the detector is missing whole columns rather
than misreading them. `UPSCALE_BELOW_HEIGHT` triggers on crop *height*, which a
multi-column vertical block clears easily while its glyphs stay tiny -- but
firing it there would not help either, because RapidOCR already scales the crop
internally (`limit_type: min`, 736px). There is no upscaling left to do.

## Harvesting originals from the Resource Timing buffer

A reader can paint pages onto a canvas while still downloading them as ordinary
images. `extension/content.js: findResourceImages` finds those, and they are far
better than a screenshot -- measured on bilibili: **1100x1699 AVIF originals**
against a **589x913** screen capture, i.e. ~3.5x the pixels, lossless.

Two mistakes to avoid, both made and fixed here:

- **Filter to images by exclusion, not by size.** A filter written as
  `if (!isImage && size < min) continue` keeps a 537 KB JS bundle, because it is
  not an image *and* not small. Exclude `.js/.css/.wasm/...` explicitly first.
- **Rank candidate hosts by count, not bytes.** Cross-origin responses without
  `Timing-Allow-Origin` report `decodedBodySize: 0`, so on exactly the hosts
  that matter a byte ranking sorts by zeros and picks arbitrarily.

Together these picked `s1.hdslb.com` (19 script files) over `manga.hdslb.com`
(3 real pages), so every fetched "image" failed to decode.

The page itself **cannot** fetch these (CORS); the extension's service worker
can, because it holds `host_permissions`. Always probe-and-verify before
trusting the mode: where payloads are transformed rather than served as images,
the decode check fails and screen capture is used instead, which is how the
protected case stays protected without special-casing it.

## Walking a paged reader (measured on bilibili)

A paged reader keeps only the current view in the DOM, so one scan sees 2
panels rather than the chapter. Walking it has four traps, all hit here:

- **Synthetic keyboard events do not work.** A content script cannot produce
  trusted input, and bilibili ignores dispatched `KeyboardEvent`s. Playwright's
  `keyboard.press` works only because CDP delivers real input. The walk must
  therefore click a real element.
- **The control is not called "next".** bilibili names it `btn-right` /
  `pagedown`. A pattern matching only `next|下一|›` finds nothing.
- **The toolbar auto-hides, and its buttons stay in the DOM.** `findNext` keeps
  returning an element while the click does nothing. Reveal the toolbar
  (`显示工具栏` / `[class*=opener]`) *every* time, and skip elements that are
  `visibility:hidden`, `display:none` or `opacity:0`.
- **The pager text is not a usable signal.** It lives in that same hidden
  toolbar, so it reads null most of the time, and several unrelated "N / M"
  widgets exist -- taking the first match compares different elements run to
  run. Prefer the largest total, and detect movement by *new page images
  arriving* instead.

Verified: reveal + click `SPAN.pagedown` loads 3 new `manga.hdslb.com` images
per step.

## Screen capture includes whatever floats over the page

A viewport screenshot bakes the reader's own furniture into the saved page --
bilibili's breadcrumb, its floating toolbar, and its `bullet-screen` danmaku
layer -- and afterwards nothing can separate that from the comic.

Do **not** identify chrome by `position: fixed|sticky`: measured on bilibili,
that matches zero elements, because the overlays are `absolute` siblings inside
the same container as the canvas. Use geometry instead -- hide anything that
overlaps a panel with real area (>8px both axes, since the two panels of a
spread touch at one edge) and is neither an ancestor nor a descendant of it,
skipping any element that holds artwork of its own.

Restore the inline `visibility` afterwards, and verify the artwork is unchanged:
hiding an ancestor hides the comic. The check that matters is that the middle of
the panel differs by 0 before and after.

Better still, avoid the problem: **resources mode has no UI in it at all**,
because it takes the originals the reader downloaded rather than a photograph.

## Capture through CDP, not through the extension

Measured on the same canvas, same page, same moment:

| path | peak luminance |
|---|---|
| `chrome.tabs.captureVisibleTab` (extension) | **153** |
| element screenshot over CDP (`comic capture --chrome`) | **255** |

The extension's screen-capture mode dims every page to ~60% and there was no
way to talk it out of it. `comic capture --chrome` is therefore the supported
path for canvas readers: it screenshots the panel *element*, which also means
toolbars and danmaku never appear and nothing has to be hidden.

The extension remains useful for its **image-URL** and **original-images**
modes, and for stepping a reader by hand. Do not spend more time on its
screen-capture path.

## The toolbar dims the artwork (bilibili)

Opening bilibili's toolbar (显示工具栏, class `ui-opener`) dims the page so the
controls read over it. Captures taken with it open come back at ~60% brightness
-- measured peak luminance 153 instead of 255 -- and white speech bubbles never
reach white, which is exactly the contrast OCR depends on.

This was self-inflicted: `revealToolbar()` clicked that button before every
advance. Navigation needs the toolbar; capture must not have it. So
`setToolbar(true)` before navigating, `setToolbar(false)` before capturing.

Match that toggle narrowly. A pattern including `opener` also matches
`icon-open` / `icon-close`, which belong to an unrelated panel: clicking those
appears to succeed while the toolbar never moves.

Detect toolbar state from the page slider's width -- collapsed, it stays in the
DOM at zero width.

If pages were already captured dimmed, `comic import-raw --normalize` applies a
linear stretch back to full range (153 -> 255). It is **off by default**: the
workspace copy is the verbatim archive of what the source served.

## Acquisition ladder

Try in order; each rung is independent of the one above.

1. **auto** — `comic download <url> --series X`. Headless browser, network capture.
2. **chrome** — `comic chrome`, then `comic download ... --chrome`. Reads through
   a browser the operator signed into and the host has already cleared.
3. **capture** — `comic capture <url> --series X`. Screenshots rendered panels,
   for readers that paint rather than serve their pages.
4. **manual** — the `extension/` Chrome extension, then `comic import-raw`.
   Independent of the site's structure entirely, and the only rung that always
   works. Prefer it over more site-specific debugging.

`import-raw` needs no `--from`: it checks `raw/<series>/<chapter>/` then the
extension's `~/Downloads/comic-raw/<series>/<chapter>/`.

A manual import has no adapter to inherit from, so it defaults to Japanese,
paged and right-to-left -- wrong in all three respects for a Korean webtoon,
and not inferable from the files. Pass `--language ko --kind longstrip`
(which implies `ttb`) or OCR gets the wrong engine and the strip is never
re-cut.

### Scrolling

Never assume the window scrolls. Measured on bilibili: `document.scrollHeight`
is 0 while 76 panels live inside a container, so every `window.scrollBy` is a
no-op and no pages load — silently. Find the real scroller first (largest
element with `overflowY: auto|scroll` and `scrollHeight > clientHeight`), then
set `scrollTop` on it. Prefer `scrollIntoView({block:'start'})` over
`scrollIntoViewIfNeeded`, which may legitimately do nothing.

Use `removeprefix`, not `lstrip`, to strip `"Chapter_"` — `lstrip` takes a
character set and eats the `E` off `Extra`.

## Adding a site

Site support is data, not code. Drop a YAML file in `comic/downloader/sites/`:

```yaml
match: "example.com"
page_kind: longstrip        # longstrip | paged
reading_direction: ttb      # ttb | rtl | ltr
container_selector: "#readerarea, .reading-content"
min_image_width: 600
chapter_number_regex: "chapter-(\\d+(?:\\.\\d+)?)"
requires_login: false
```

The generic importer handles lazy loading, scroll-triggered loading and expiring CDN
URLs by capturing image bytes off the network as the browser receives them. Write a
`.py` adapter only when YAML genuinely cannot express the site.
