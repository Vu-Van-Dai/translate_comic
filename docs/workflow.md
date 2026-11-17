# The chapter workflow (human in the loop, since 2026-11-08)

The operator reviews boxes and does the typesetting; the agent checks transcriptions and
translates. This replaced `comic render` + `visual_qa` for new chapters (that route still
works and is documented in CLAUDE.md, but nothing new goes through it).

```
download -> analyze -> STUDIO (operator) -> check -> translate -> clean -> ps build
         -> PHOTOSHOP (operator, one review) -> ps publish
```

| # | Step | Command | Who |
|---|---|---|---|
| 1 | Download / import | see `docs/acquisition.md` | agent |
| 2 | Detect + OCR | `comic analyze --series S -c N` | local PC |
| 3 | Box review | `comic studio [--series S -c N]` | **operator** |
| 4 | Transcription check | `comic check --series S -c N [--agent] [-p P ...]` | agent / AgentRouter |
| 5 | Translation | `comic translate --series S -c N [--agent] [--missing] [-p P ...]` | agent / AgentRouter |
| 6 | Cleaning | `comic clean --series S -c N [--local]` | local + Colab T4 (LaMa) |
| 7 | PSDs | `comic ps build --series S -c N` | local + Photoshop |
| 8 | Typesetting, QA | Photoshop | **operator** |
| 9 | Publish | `comic ps publish --series S -c N` | WebP into `library/` |

## 3. Studio

A local browser app on 127.0.0.1:8765 (`comic/studio/`). The operator moves, resizes, adds and
deletes boxes and labels them: dialogue, thought, narration/caption, shout, sfx, logo, watermark,
credit (logo/watermark/credit = `erase` + `tag`), signage (`keep`), ignore -- keys 1-9 and 0 --
and, on key **L**, "SFX xóa (LaMa)" (`sfx` + tag `lama`, `TextRegion.lama_sfx`; the operator
2026-11-14): an SFX that Content-Aware Fill would botch. It is checked, translated and set like any
other SFX; only its erasing moves -- LaMa takes it at cleaning (whole box, never a flat fill) and
`ps build` plans no fill for it (`SfxItem.pre_erased`). Left untranslated, its spot is just the
cleaned art (Original + Cleaned). Applies to chapters boxed from 2026-11-14 on; nothing earlier is
re-cleaned. The interface is in
Vietnamese; stored labels stay English. Saving writes `analysis/page_NNN.json` (the detector's
version is kept in `analysis/_detected/`); reviewed pages are listed in `studio.json`
("Duyệt cả chương" marks all). `comic check` refuses a chapter with unreviewed pages unless
`--unreviewed-ok`.

## 4. Transcription check

`comic check` writes contact sheets of every dialogue/narration/thought/shout/caption/SFX crop
to `handoff/check/` plus `check_request.json`. Logos and watermarks are not checked. The answer
(`check_response.json`) names only regions that change: `text`, `kind`, or `skip` (left for the
operator: kept, cleaned, never translated, listed in `untranslated.txt`). It never deletes a box.
`comic resume` validates and applies it.

## 5. Translation

Pure translation of dialogue and SFX (meaningful SFX only; the rest stay as drawn). Forms of
address are pinned per series (CLAUDE.md); an established pair always wins. After a translation
is applied, `workspace/<series>/<chapter>/untranslated.txt` and the series' `untranslated.txt`
list every line not translated: series, chapter, page, region id, kind, position, source text,
reason.

`--missing` asks only for regions with no Vietnamese yet (a re-cut chapter, a partial run).

## AgentRouter add-on (while the credit lasts)

`--agent` on `comic check` / `comic translate` has an AgentRouter model answer the gate through
the Codex CLI (`comic/agents/`; prompts in `comic/agents/prompts/`, reviewed by the operator),
then applies it with `comic resume` -- validated exactly like an answer written by hand. The key
lives only in the git-ignored `.secrets/agentrouter.key`. Calls are batched and paced; images go
as JPEG at most 1600 px. Held pages are never in a request, so never sent. When the credit is
gone, drop `--agent`: the agent answers the same gates itself.

## 6. Cleaning and Colab

`comic clean` erases every erasable region, translated or not. Flat fills happen on the spot;
glyphs over artwork (characters, hair, trees, screentone) are batched into **one** LaMa job for
the Colab T4 (`anime_manga_lama.pt`), or run locally when no worker is alive (`--local` forces it).

Only LaMa goes to Colab: the T4's CPU is slower than the PC's, and detection + OCR measured
26 s local vs 84 s through Colab for the same output. Files move through Google Drive for desktop
(`G:\My Drive\comic_worker`, created by the PC only), never ngrok. The operator opens
`comic_worker/comic_gpu.ipynb` and runs all cells; `comic colab push|status|stop-drive` manage
the rest.

SFX boxed with Studio's key L (`lama_sfx`) are erased right here, like the marks below (whole
box, LaMa, never a flat fill); `ps build` still sets their Vietnamese but erases nothing more.
Other SFX: Photoshop Content-Aware Fill first (`comic ps build` plans it); LaMa where fill falls
short -- near-vertical SFX (erased, the note says to set it by hand). Logos, watermarks and
credits are erased by `comic clean` itself, always with LaMa and never a flat fill (the
operator, 2026-11-12: flat fills lost story detail): the whole box, 3 px wider; on a
black-and-white page also every coloured pixel touching it (a glowing logo's halo reaches past
the box); a hole at the window's edge gets mirrored context; a page counts as grey with its
marks set aside, so LaMa's colour is never kept there (the blue smear on Ku Ku ch.1.1 p.2).
Tracing strokes inside a mark box was tried and dropped: faint outlines left ghosts. `--clean
hybrid` leaves those LaMa repairs alone.

**Every box labelled logo / watermark / credit is erased, with or without a reading**, and so is
any box the operator drew or reshaped in Studio (`TextRegion.cleanable`). Until 2026-11-12 an
empty reading skipped the box, so every watermark drawn by hand in Studio survived cleaning
(358 pages). An empty reading still means "leave it" only on an untouched detector box. A page
whose only text is such a mark gets a PSD too, and `ps publish` refuses to fall back to the
original for it -- the original would put the mark in `library/`.

A page held for its content with `comic exclude --held --cleanup` is cleaned like any page and
gets its PSD (no text): the operator letters it by hand. Only the gates skip it -- the agent never
transcribes or translates it -- and `ps publish` keeps whatever file the operator put at its number
(2026-11-13, JJ ch.3; until then held pages were skipped and kept every group mark).

What is erased: other groups' (CN/EN/KR/JP scanlators') logos, watermarks, credits and inserted
site links, on story pages. What stays: bilibili's own logo and "BILIBILI COMICS EXCLUSIVE"
notice (`keep`), and credit or warning pages at the very end of a chapter, published as drawn.

## 7-9. Photoshop and publishing

`comic ps build` writes one PSD per page: `Original` (visible, bottom), `Cleaned` above it
(opacity adjustable), groups `SFX` and `Thoại` with editable text in the series' Typer Tools
styles. A PSD the operator has saved since its export is never rebuilt
(`--overwrite-edited` only on request).

**Finish everything first** -- cleaning, LaMa, SFX, complete PSDs -- and only then open the PSDs
for the operator's single review, naming the pages with flagged spots. Publish only after the
operator approves: `comic ps publish` re-exports every PSD saved after its PNG, then writes WebP
q95 into `library/` with the `000` poster.

## Re-cutting a strip whose slices were saved out of order

Early comix downloads (My Insatiable Demon, Walking in the Rain, Dare mo) saved some slices out
of order, cutting bubbles at the seams. `workspace/_tools/comix_fetch.py` now orders slices by
the reader's page number. To repair a chapter:

1. Fresh download with the fixed fetcher; `scripts/fix_slice_order.py S N <fresh dir> --check-only`
   reports whether the order differs (`workspace/_tools/comix_check.sh` loops it).
2. `fix_slice_order.py S N <fresh dir>` backs up to `_backup_before_reorder/`, reorders the
   originals and `raw/`, re-cuts, and keeps every page that comes out pixel-identical to an old
   one (under its new number, region ids renumbered): analysis, reviewed mark, translations.
   Per-page outputs (`cleaned/`, `translated/`, `photoshop/`) move into the backup. It prints the
   pages to re-detect.
3. `comic analyze -p ...` on those pages; Studio review of those pages; `comic check -p ...`.
4. `scripts/carry_translations.py S N` gives re-cut lines their old Vietnamese when the English
   matches; `comic translate --missing` translates the rest (bubbles the old cut had split).
5. `comic clean`, `comic ps build`, review, `comic ps publish`.

`scripts/seam_check.py S` finds suspicious seams without downloading anything.
