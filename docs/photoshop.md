# Photoshop route (`comic ps`)

Read this before running any `comic ps` command: building PSDs, the SFX pass, notes,
watermark erasing, and publishing as WebP.

Moved here from CLAUDE.md word for word (2026-10-20) so the everyday rules stay
short; CLAUDE.md links to this file. The rules below are as binding as CLAUDE.md's.

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
