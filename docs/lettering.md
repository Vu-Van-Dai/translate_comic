# Lettering rules

How Vietnamese is set into a page, for every series. Two sources, both from
the operator:

- Nate Piekos, *The Essential Guide to Comic Book Lettering* (Image, 2021) --
  `Essential Guide to Comic Book Lettering.pdf` in the repo root. Chapter and
  page numbers below refer to it.
- editmanga, "Những quy tắc Typeset cơ bản cần biết"
  (<https://www.youtube.com/watch?v=pqXcbWdp-E4>), the Vietnamese scanlation
  view of the same craft.

Each rule says what the pipeline does about it. Where it says "the agent",
the decision is made at a gate; the code only carries it out.

## 1. One lettering style guide per series (Piekos ch. 6, p. 156)

Before the first chapter, decide the faces, caption treatment and SFX
treatment for the series from its genre and art, and keep them for the whole
run. That is `profiles/<series>/fonts.yaml`, which normally just names a
**format preset** and overrides what differs:

```
preset: manhwa        # manga | manhwa | manhua | western
```

`profiles/_presets/`: `manhwa` (AnimeAce3 caps, its italic for thought and
narration, bold italic shout), `manhua` and `western` (same, until a chapter
shows otherwise), `manga` (sentence-case Mali for narrow vertical bubbles,
auto_contrast for inverted panels -- a starting point to confirm on the first
manga chapter). Each preset pulls in `voices.yaml`, the shared mood library.
Changing a series' choice mid-run is an explicit edit, like the pronoun table.

**Keep the set small** (the operator): one face per mood, per format -- not
every font that passes the checks. A new face earns a place by covering a
mood or a format the set cannot.

## 2. Faces

| Use | Style (Piekos ch. 4, pp. 70-73) | `fonts.yaml` |
|---|---|---|
| Normal speech | the dialogue face, **regular** weight | `styles.dialogue` |
| Thought balloons, internal monologue and narrator captions | **italic** of the dialogue face | `styles.thought`, `styles.narration` |
| Emphasis, yelling in a normal balloon | **bold italic** | `styles.shout` |
| Real screaming, shout balloons | a beefier, "shouty" companion face, used with restraint | a voice |
| Whisper | same face at ~75% in a roomy balloon ("small text / big balloon", p. 126) | a voice with `size_scale` |
| Notes, journals, phone messages | a handwritten or sans face (pp. 153-155) | a voice |

- **All caps** is the comic convention (p. 66); set `uppercase: true`.
  **But case belongs to the voice**: Vietnamese groups set the soft cloud
  bubble in a rounded hand (Augie) and phone messages in a plain sans,
  both in sentence case, beside caps dialogue -- `soft`, `scrawl` and
  `phone` say `uppercase: false`. A sentence-case voice needs a face with
  real lowercase; most TeddyBear caps faces draw lowercase as capitals.
- **Do not copy the raw's face** (editmanga): Vietnamese is a different
  alphabet; pick what reads well in Vietnamese, not what looks like the source.
- **Fit the face to the delivery, sparingly** (editmanga; the operator).
  Ordinary speech takes its kind's face. A `voice` -- `cheerful`, `excited`,
  `angry`, `menacing`, `hesitant`, `soft`, `whisper`, `scrawl`, `phone`
  (`profiles/_presets/voices.yaml`, listed in `translate_context.md`) -- marks
  a real peak, and holds for the run of bubbles in that mood instead of
  switching per bubble. Aim for two or three faces a page: Super Lovers ch.1
  set first with a voice on most bubbles, five faces on one page, and read
  as random (Piekos p. 76: "a little goes a long way"). `restyle` at
  visual_qa changes a choice.
- **Consistency over expressiveness** (the operator, after Super Lovers
  ch.1): bubbles joined into one shape read as one bubble and must share a
  voice -- p.4 set "Thế nên mai anh về Nhật Bản rồi." in the soft hand and
  the joined "ÍT NHẤT TỐI NAY..." in caps, and it looked broken. Check
  joined bubbles at visual_qa.
- **Coverage need not be complete.** Missing punctuation is substituted
  (`coverage.SUBSTITUTES`: dashes, guillemets, ellipsis, curly quotes,
  brackets). A face missing a *letter* is still usable: a line that needs
  that letter falls back to its kind's face (`render.missing_for`), so it
  never prints tofu or a different word.
- **A cmap is not a drawing.** Fan conversions map toned letters to the
  wrong glyph; `coverage.tone_defects` renders each vowel's six tones and
  treats twins and blanks as missing. Always look at a rendered sample too:
  one face set "ĐẾN" as "ĐẾ N" (spacing), which no check catches.

## 3. Size and leading (pp. 63-65)

- There is no standard size; what matters is legibility, and "when in doubt,
  go a tiny bit larger". The pipeline caps each line near the source
  lettering's own size (`source_size_cap`, times `size_scale`).
- **Leading:** lines must never collide, then add a little room -- and it
  belongs to the typeface. Vietnamese stacks accents, so the span from the
  tallest accent to the lowest dot below (`Ỗ` over `Ộ`) is measured per face
  (`fit.leading_em`): `leading: auto` sets that span plus 0.06 em, never
  below 1.22 em. AnimeAce3 spans 1.05 (keeps 1.22); the expressive faces
  1.28-1.59; Bangers 1.60. Off by default so published series keep their
  leading (Be Vietnam Pro would go from 1.22 to 1.44 em).

## 4. Breathing room (pp. 67, 79, 150)

"The negative space inside a caption is typically about the size of an
uppercase O from the font you're using", and a balloon butted against a panel
border keeps about a line of room. `pad_em` keeps that margin on **every**
side; set it to the face's cap height (AnimeAce3: 0.6 em). Text set against
a bubble's outline was the first complaint from a reader.

## 5. Stacking lines (pp. 68-69, 150)

- Balloon text stacks into the balloon's shape: the longest lines in the
  middle, no tiny word alone on top, no lopsided negative space.
  Caption text breaks "in the most rectangular shape you can".
- editmanga: **follow the bubble's own shape** -- a square box takes a
  squarish block, not the "egg" every time.
- The fitter wraps into the bubble mask itself, and among wraps that fit at
  the same size it prefers the one whose emptiest line fills most of its row
  (`fit._evenness`), then no widow, then fewer lines. A true balanced
  oval stack (a line-breaking pass in its own right) is not built yet.

## 6. Breaking lines (p. 65; editmanga)

- **Never hyphenate**, never break a word. In Vietnamese every syllable is
  written apart, so "a word" is often two tokens: the agent binds them with
  a no-break space (U+00A0, `hôm nay`) and `fit.wrap` will not split them.
- **Meaning before shape** (editmanga): a line broken for a prettier outline
  that reads wrong is worse than a plainer shape that reads right. When the
  automatic break splits a phrase badly, fix it with `force_wrap` at
  visual_qa.

## 7. Captions (ch. 6)

- Spoken captions (a voice heard off-panel) are quoted; internal monologue
  and narration are italic; location/time captions are styled distinctly.
- Text messages: sans face, light text on a coloured rounded box (p. 155).
  In-world screens and signs stay as drawn unless they carry the plot (see
  CLAUDE.md, Photoshop route, "What stays in English").

## 8. Sound effects (ch. 7) -- Photoshop route only

- Translate the *quality* of the sound into the letters: loud and close is
  big and dynamic, soft is small and quiet (p. 169).
- Condensed letters where space is tight, extended where it is short and
  wide (p. 170).
- An SFX crossing inked lines needs an outline (offset path, p. 173); match
  the original's fill, outline and angle (CLAUDE.md, "Sound effects").

## 9. Case guideline (from Vietnamese groups, studied 2026-10-08)

The operator's rule: **fonts are taste -- groups pick their own and keep
them out of habit -- but case follows shared conventions.** Measured over
seven chapters from two groups: Roads Team (webtoons *Cùng nhau sống sót*
ch.65, *Thức uống tình yêu của Flynn* ch.83, *Những tên ngốc* ch.2, *Winter
Field* ch.1, *Chồng mới cưới của tôi là rồng vàng* ch.1) and ReD Frog
(manga *Nhõng nhẽo* ch.1, *Mê cậu thêm nữa là đời tôi toang* ch.1). The
operator confirms published books follow the same pattern: caps for
standard dialogue, other case only in specific cases. Publishers' own
sites show covers only, so no printed page was measured directly.

| Text | Case | Seen |
|---|---|---|
| Speech bubble, any shape (round, jagged shout, dark, coloured) | **CAPS** | all 7 chapters, without exception |
| Thought bubble (cloud, fuzzy, dotted, spiky-dark) | **CAPS** | all 7 |
| Emphasis inside a bubble | caps, set **larger or bolder** -- never a case change | Cùng nhau sống sót, Winter Field |
| Narration / inner-monologue caption box | **series choice**: caps (most Roads webtoons) or sentence case (ReD Frog manga, Winter Field) -- one rule for the whole series | split |
| Small aside lettered on the art, no bubble ("Anh á?", "Nóng quá") | **sentence case** | both groups |
| Small cute/teasing/pleading bubble beside chibi art, the soft cloud | sentence case | Flynn, Những tên ngốc, Cùng nhau sống sót |
| Phone / chat messages | sentence case, plain sans | Flynn |
| In-world printed documents (notice, letter, file) | as the document would be printed -- headings caps, body sentence or caps | Mê cậu, Cùng nhau sống sót |
| Translator's footnote (*Bias: ...) | sentence case, small | Mê cậu |
| SFX | translated always; loud impacts caps (RẦM, ĐÙNG, NGÃ), soft or continuous sounds mixed/lower case (Thình thịch, Hộc, bước, Lạch cạch) | all 7 |

Constants across both groups:

- **One case per bubble**, and joined bubbles share it.
- **One dialogue face for nearly every bubble**; a different face marks a
  different *kind* of text (caption, aside, phone, SFX), rarely a mood.
- Generous room inside bubbles; type never touches the outline.

Per series, decide once and record in `fonts.yaml`: caption case, and
whether the series has a cute/chibi register that earns sentence-case
bubbles. Everything else follows the table.

## 10. Common manga editing errors, and what handles each (from the operator's review of Super Lovers ch.0)

| Error | Fix in the pipeline |
|---|---|
| A white patch pasted over dot tone or shading | cleaning erases letters only, never the bubble (`clean_page`) |
| Text faint or lost on dots / shading | 0.2 em halo of the paper colour over texture (`render.haloed`); sentence-case lines in a bold rounded face (Augie), not a thin one |
| Text too small for its balloon | size follows the balloon: a block filling under 30% of a roomy balloon may grow to 1.35x the source size but never past covering half of it, and never inside the 0.6 em margin (`render.layout_region`), with an 85% page-median floor as a safety net (`render.layout_page`); a toned box is measured whole (`mask.toned_interior`) |
| Captions on one page at different sizes | captions share the page median as a ceiling |
| Ghost letters beside a hand-lettered aside | wider erase for lettering with no bubble (`clean.ART_EXTRA_GROW`) |
| Text running over a balloon edge / into the next balloon | `force_wrap` + `nudge` at visual_qa; check joined balloons by eye |
| An aside on the art left in English | look for lettering the detector missed; add it as a `kind:` item with a `bbox` in the Photoshop route |

Colour pages see fewer of these, since most colour balloons are plain white.
