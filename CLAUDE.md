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

Tier choice is measured, not guessed from the region's kind: a flat light area
is filled with its own modal colour, which erases the text and is a no-op
everywhere else -- and keeps the bubble outline, which an inpainting model will
not. Everything else inpaints the glyphs only. Measured on these pages, 35 of
37 regions were flat fills.

`clean_page` returns the union of every pixel it touched, so the
artwork-preservation guarantee is asserted rather than assumed. **Scope the QA
residue check to that same glyph mask**: measuring leftover ink over the whole
bubble counts the outline and neighbouring artwork, and reported 60-73%
"residue" on regions that had in fact been erased completely.

## Typesetting into a shape

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

## Access-gated sites (measured 2026-09-20)

| Host | Plain automation | Route |
|---|---|---|
| comix.to | 200, pages load | direct, but lazy-loads — see note |
| manga.bilibili.com | 200 | needs the operator's login |
| theblank.net | 403 Cloudflare interstitial | `--chrome` |
| yaoiscan.com | 403 Cloudflare interstitial | `--chrome` |

A bot challenge is a **terminal outcome**, raised as `BotChallengeError`. Do not
add stealth patches, fingerprint spoofing or CAPTCHA solving to get past one —
that is circumventing an access control and is out of scope for this project.
The supported route is `comic chrome` + `comic download --chrome`, which reads
through a browser the operator signed into and the site has already cleared.

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

Three traps, all hit here:

- **Do not decide "is this a strip?" by height.** The usual export step is
  *shorter* than a page, so every file passes a height test while half the
  joins run through artwork -- which is exactly the case slicing exists for.
  Ask what the existing joins look like instead: a file that ends where a page
  ends, ends in gutter. `needs_slicing` re-cuts only when most joins do not.
- **Measure the gutter colour; do not assume white.** Plenty of webtoons
  letterbox on black. `gutter_level` takes the modal level of flat rows, and a
  flat band of some *other* colour is flat artwork -- a tinted caption box, a
  night sky -- where cutting splits something rather than nothing.
- **Never hold the strip in memory.** A chapter is routinely 800x60000, which
  is 144 MB in colour. Seam-finding needs one number per row, so slices are
  measured one at a time and re-read when pages are emitted.

A page with no flat band in range is cut at the target height anyway, with the
seam recorded as `band=0` and reported. Refusing would leave a 60000px "page"
no later stage can handle; cutting silently would hide that a bubble may have
been split.

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

**Caveat, and it is the same one as the resolution curve.** Every Korean
number here is on *cleanly rendered* Malgun Gothic in plain white bubbles, not
on real webtoon lettering, which is stylised, tightly set and often over
artwork. Chinese scored 1.000 on equivalently clean material and 0.663 on the
real thing. So treat 0.985 as evidence the engine and the wiring are sound,
not as the accuracy a real Korean chapter will show -- and budget for the
review gate accordingly until one has been measured. `scripts/ocr_bench.py`
will score it the moment a chapter has been through the gate.

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
