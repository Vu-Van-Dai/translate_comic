# OCR, detection and webtoon slicing

Read this before changing detection, OCR or slicing code, before working with a
language for the first time, or when OCR results look wrong.

Moved here from CLAUDE.md word for word (2026-10-20) so the everyday rules stay
short; CLAUDE.md links to this file. The rules below are as binding as CLAUDE.md's.

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
