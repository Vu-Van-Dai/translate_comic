> Research only (2026-10-20). Nothing here is decided or built; each idea needs the operator's OK, and the bugs listed need confirming before a fix.

# Token-saving ideas for the comic-translator pipeline, ranked (2026-10-20)

**Units.** Savings are in input-equivalent tokens (ie) per chapter unless stated otherwise.
- Image tokens = ceil(w/28) × ceil(h/28).
- Cache read = 0.05×, 1-hour cache write = 2×, output = 5×.
- A token that stays in context costs about 2 + 0.05 × (later turns).

**Status labels.**
- **keep** / **uncertain**: the review pass gave that verdict.
- **unreviewed**: came from the harness-levers stream, which had no review. For those I re-checked the product claims against the current Claude Code and API docs. What I could not check is marked.

Nothing here has been measured end to end. `scripts/session_cost.py` is already planned, and it should be built first so these numbers can be replaced by measurements.

## Bugs to fix whatever they save
These turned up during the research. Each one can cause a defect the operator ranks top.

- **#5 Overlay numbers are wrong after any drop at ocr_review.** A line can be matched to the wrong bubble or speaker.
- **#11 `retranslate_shorter` erases the line's settings.** A shortened answer that leaves out speaker, addressee, note or voice silently wipes them (`apply_translation` replaces the whole entry).
- **#14 `comic analyze --force` wipes reviewed work.** It loses the agent's transcriptions and drops, and brings dropped regions back.
- **#7(c) Some SFX are never offered at the SFX gate.** Regions the classifier itself labelled `kind=sfx` never appear there, and an item naming one is rejected.
- **#6 `leftover_scan`'s regex misses most English SFX words.** SLAM, THUD, BAM, GASP, DRAG and others do not match, and it never flags non-English lettering.
- **#17 LaMa gets the wrong input.** It is fed BGR, which matters on the colour webtoons now in progress. In the inpaint tier it also never erases glyph pixels outside box ±32 px.
- **#21 Tall pages reach Claude downscaled.** Once more than 20 images are in a request, pages over 2000 px are no longer seen at full size. The slicer emits pages up to 2600 px (`strip.py:72`). This answers the "open check" in `token-savings.md`.

## Summary

| # | Idea | Est. saving per chapter | Status | Operator decision? | Effort |
|---|---|---|---|---|---|
| 1 | visual_qa round 1 in page batches | ~150-500k (simulated) | unreviewed, mechanism in docs | only for the subagent variant | small (A) to medium |
| 2 | Settings bundle (git text, suggestions, Concise) | ~80-120k (estimate) | unreviewed, settings exist | no | small |
| 3 | Memory hygiene | ~18-90k | unreviewed | archive move | small |
| 4 | Gate request JSON without null fields | ~25-40k (more with one-line items) | keep | one-line items and relative paths: yes | small |
| 5 | Overlay labels use the region's own number | ~20-50k (unmeasured), mainly correctness | keep | no | small |
| 6 | `leftover_scan` on `NNN_art.png`, plus script fixes | ~2k to 30k+ (phase 2) | keep + uncertain | tell operator (step change) | small / medium |
| 7 | SFX gate keeps the ocr_review verdict and its own answers; lists `kind=sfx` | ~10-25k | keep | no | medium |
| 8 | Trim ocr_review contact sheets to their used width | ~10-15k | keep | packing only | small |
| 9 | English recogniser (PP-OCRv5 en) | ~8.5-17k per English chapter if the bench confirms | uncertain | adopt after bench | small |
| 10 | Fused-word readings: auto-space or stop flagging | ~20k (unmeasured) | uncertain | yes (reverses a design rule) | small-medium |
| 11 | retranslate_shorter carries the current line and its fields | 0-21k (0-1 rounds) | keep | no | small |
| 12 | Second detector (RT-DETR) auto-drops flagged artwork | up to ~9-12k per webtoon chapter, 0 on paged | uncertain | yes (stop-and-ask rule) | medium |
| 13 | Review a repeated reading (watermark) once | ~6-10k when a watermark exists | uncertain | cross-chapter auto-drop only | small-medium |
| 14 | `analyze --force` keeps reviewed work | ~85k per occurrence, frequency unknown | keep | no | small-medium |
| 15 | Shorter validation errors and progress output | ~2-6k steady, ~20-30k per failed resume | keep | no | small |
| 16 | Leftover-lettering pre-check inside `comic render` | ~18-20k per avoided QA round | uncertain | no | medium |
| 17 | Fix how LaMa is fed | ~0-5k (gain is quality) | uncertain | no | small + bench |
| 18 | Manga-finetuned LaMa per series | ~0-10k | uncertain | yes (per series, licence) | medium |
| 19 | Switch High to Medium with `/effort`, never `/model` | 0 expected, avoids a ~200k one-off | unreviewed, docs-verified | no | none |
| 20 | Never Monitor or poll `comic` runs; 1-hour cache if usage credits are on | 0 expected, avoids 125-245k one-offs | unreviewed, docs-verified | no | none |
| 21 | Gate images: lossless WebP, ≤2000 px (tall pages in two halves) | ~0 tokens; quality, and lets #1 batch more | unreviewed | no | small |

---

## 1. visual_qa round 1 in page batches that fit Claude Code's image budget
**What.** Judge round 1 in batches of about 8 pages (16 images: rendered page plus original), one batch per conversation. There are three variants:
- **(A) No code.** Each batch appends its fixes to `handoff/visual_qa_response.json`. The operator runs `/clear` between batches. Only the last batch sets `passed` and runs `comic resume`.
- **(B) Code.** The request lists the batches, and resume merges one file per batch.
- **(C) Automatic.** One foreground subagent per batch: `model: inherit`, Read only, `omitClaudeMd` with the visual_qa rules in the agent body, `maxTurns`.

This complements the already-decided change (3), which covers rounds 2 and later only.

**Why it saves.** Claude Code caps the images in one request. When the next request would exceed the cap, it removes a batch of the oldest images, and "the next request reprocesses the conversation from the earliest of those messages onward". That is close to a full re-write of the context at 2×. Smaller conversations also re-read less context on every turn.

**Saving.** A page of 850×1250 px is 31 × 45 = 1,395 tokens, and round 1 shows two images per page. The researcher's simulation:
- 30 pages: one conversation ~582k (6 evictions); 8-page batches ~323k, i.e. −45% (~260k).
- 50 pages: 1,049k → 541k.
- Variant C: ~270k for 30 pages.
- With page files of 0.6-1.5 MB: −38% to −44%.

Caveats:
- The byte cap (24 MiB of base64) comes from the binary's strings, not the docs.
- Small black-and-white PNGs (~0.4 MB) fit about 45 images (~22 pages) before any eviction, so short paged chapters save much less.
- Not measured.

**Quality risk.** Neutral to positive. Every page is still judged beside its original. Today, pages read after roughly the 10th image silently lose sight of earlier ones anyway. Give each batch the chapter's pre-check summary so cross-page size consistency is still covered.

Variant C carries these risks:
- the subagent must carry the full visual_qa rules and run on Opus;
- there is a known bug where background subagents loop after eviction (anthropics/claude-code#101964), so run them in the foreground, keep batches under the cap and set `maxTurns`.

**Rule conflicts.** A and B: none. They extend the decided "one gate per conversation", at the cost of extra `/clear` steps. C: the operator should say whether Claude Code subagents are acceptable, given CLAUDE.md's "no sub-agent orchestrator" (written about `comic/`, but close).

**Effort.** A small (instruction text only); B and C medium.

**Integration.**
- `comic/qa/visual.py: open_visual_qa` (page loop around 398-416, instructions around 428-452).
- `comic/handoff/schemas.py: VisualQaRequest` (add `batches`).
- `comic/cli/app.py: _open_render_gate` and `_resume_visual_qa`.
- Variant C: `.claude/agents/visual-qa-batch.md`.

**Evidence.**
- https://code.claude.com/docs/en/prompt-caching#accumulating-many-images (verified)
- https://code.claude.com/docs/en/prompt-caching#subagents-and-the-cache (verified: subagents get 5-minute TTL even on a subscription)
- https://platform.claude.com/docs/en/build-with-claude/vision#request-limits
- https://github.com/anthropics/claude-code/issues/101964

## 2. Settings bundle
**What.** Add these to `.claude/settings.local.json` (or the user settings file):
- `"includeGitInstructions": false`
- `"promptSuggestionEnabled": false`
- `"outputStyle": "Concise"`

Also turn off unused MCP servers in `/mcp`. Run `/context` once per session type to see what actually loads.

**Why it saves.**
- **Git text.** Without it, the built-in commit/PR text is no longer re-read on every request. The operator never wants commit talk.
- **Prompt suggestions.** A suggestion request "reuses the conversation's prompt cache, so it is mostly cache reads plus a few output tokens", once after every Claude response.
- **Concise.** It "leaves out the lead-in, the step-by-step narration, and the closing recap" and "does the engineering work as thoroughly". Narration is output, priced at 5×.

**Saving (estimates).**
- Git text: ~1.4k tokens × 0.05 × ~300 requests ≈ 21k. It may already be absent from Opus 5.5's shorter prompt; check in `/context`.
- Suggestions: 1-3 responses × ~6 conversations × ~60k context × 0.05 ≈ 18-54k.
- Concise: ~30 narration tokens × ~300 tool calls × 5 ≈ 45k. This is an assumption; measure it.
- Total about 80-120k.

**Quality risk.** None on translation or QA. The gate JSON is written with Write and is unaffected by the style. Removing the git text also removes Claude's built-in git safety text. That is low risk, because git runs only when the operator asks.

**Rule conflicts.** None. It fits the no-commit-reminders memory.

**Effort.** Small. Set it once: a change to tool definitions costs one cache rebuild at the next session start.

**Evidence.**
- https://code.claude.com/docs/en/settings-reference (all three keys exist; their full entries were not read)
- https://code.claude.com/docs/en/costs#background-token-usage (verified)
- https://code.claude.com/docs/en/output-styles (Concise, v2.1.237+, verified)
- https://code.claude.com/docs/en/costs#reduce-mcp-server-overhead
- The claim that `includeGitInstructions` also drops the git-status snapshot is unverified.

## 3. Memory hygiene
**What.** Shrink what Claude reads at the start of each gate conversation:
- `current-work.md`: replace the diary with a ≤1 KB "state now" block and move the history to an archive.
- `mistakes-log.md`: split it by gate. Archive the entries now enforced in code or repeated in docs (fit_areas leak, Augie widths, off-centre now checked in render, LaMa specks, Adobe fonts folder, and so on).
- `token-savings.md`: archive the "Done" narrative.
- `MEMORY.md`: one short line per entry, plus "gate X → read file Y".
- Never edit `CLAUDE.md` or `MEMORY.md` in the middle of a run.
- Keep `docs/` as plain references; an `@import` would load them at launch and undo the split.

**Why it saves.** `MEMORY.md` always loads (first 200 lines / 25 KB). Topic files load on demand. `MEMORY.md` line 1 says to read `mistakes-log.md` "before every chapter". The three state files total about 16.5 KB, roughly 4.1k tokens (verified sizes: 5,226 + 5,714 + 5,567 bytes).

**Saving.** About 3k fewer tokens per conversation that reads them, × (2 + 0.05 × ~80 turns) ≈ 18k per conversation. That is ~18-90k per chapter, depending on whether 1 or 5 gate conversations read them; check this in a transcript or `/context`. A mid-session `CLAUDE.md` edit does not invalidate the cache; it only takes effect at the next `/clear`.

**Quality risk.** Neutral if entries are moved, not deleted. Keep the archive linked as "history, not required".

**Rule conflicts.** The operator asked for a standing mistakes log, so it stays, split and archived. Let the operator approve the move.

**Effort.** Small.

**Evidence.**
- https://code.claude.com/docs/en/memory#auto-memory
- https://code.claude.com/docs/en/prompt-caching#editing-claude-md-mid-session (verified)

## 4. Gate request JSON without null fields (merged from two streams)
**What.**
- **Unconditional.** Write gate requests (not state files) with `exclude_none=True`. Delete the dead `speaker_hint` field: nothing writes it. `panel_index` is never set, so give it `= None` first (`schemas.py:79` has no default, and a missing field would fail any read-back).
- **Separate, operator's call.** Put each item on one line, still JSON with the same keys.
- **Separate, operator's call.** Use relative crop paths. Read needs absolute paths, so Claude would have to join them.

**Why it saves.** `write_json` dumps with `indent=2, exclude_none=False` (`store.py:40`, verified). Every item therefore carries `panel_index`, `speaker_hint`, `must_fit_chars` and `shorten_reason` as nulls. Read prefixes every line with its line number, and the request is re-read on every later turn of the translate conversation.

**Saving.** Measured at real nesting: −131 chars and −4 lines per item, about 25-31 tokens. A 200-item request goes from 3,019 to 2,218 lines; both still need two Reads at Read's 2,000-line default.
- Total: 200 × ~30 = 6k tokens × (2 + 0.05 × T) ≈ 27k at T = 50 turns and 42k at T = 100.
- One item per line: about 95 tokens per region in total, roughly 3× this, but it needs the operator's yes.

**Quality risk.** Neutral for null removal: an absent optional field means exactly what null meant. One item per line has low risk but looks different.

**Rule conflicts.** Null removal: none. One-line items and relative paths sit next to the rejected compact table/TSV formats, so ask first.

**Effort.** Small.

**Integration.**
- `comic/state/store.py: write_json`, a request-only variant.
- Callers: `stages.py` around 599 (ocr_review), `translation/gate.py:191-192` (translate), `qa/visual.py` around 454 (visual_qa), `photoshop/sfx.py:318`.

**Evidence.** https://code.claude.com/docs/en/tools-reference#read-tool-behavior

## 5. Overlay labels: use the region's own number, not its position in the list
**What.** In `overlay_regions`, draw `str(region.reading_order)` (or the numeric suffix of the id) instead of the `enumerate` index. Verified at `comic/vision/analyze.py:156-163`.

**Why it matters.** After ocr_review drops a region, every later box on that page shows a number one too low. Meanwhile the translate request lists `reading_order` from detection. The overlay docstring promises "the numbers are the region ids". Claude must then open crops to resolve boxes, or it matches lines to the wrong bubble.

**Saving.** About 3.5-5.5k per look-up (crop 130 tokens + 250 for output + one turn re-reading a 60-100k prefix at 0.05), × 5-10 look-ups ≈ 20-50k. Unmeasured. The main value is correctness: this is a direct route to wrong-speaker errors.

**Quality risk.** Positive. **Rule conflicts.** None. **Effort.** Small (one line).

**Integration.** `analyze.py:156-163`. It is used by `translation/gate.py:120`. The ocr_review overlays are drawn before drops and are unaffected.

## 6. `leftover_scan`: scan the text-free art file, and widen what it catches (merged)
**What.**
- **(a) Zero code (keep).** Run the scan on the art files instead of the finished pages:
  `python scripts/leftover_scan.py workspace/<series>/<ch>/photoshop --glob "[0-9][0-9][0-9]_art.png"`
  `NNN_art.png` is the Cleaned layer with our Vietnamese and SFX groups hidden (`driver.py:221-222`). Any source lettering still visible on the final page is still visible there.
- **(b) Add the check (a) loses.** The art file also hides any English *we* set. Add a regex pass, no OCR, over `translation.json` `target_text` and the `sfx_response.json` texts.
- **(c) Script changes (uncertain).**
  - `--pages` so only rebuilt pages are scanned.
  - A per-chapter accepted-spots file, which mainly helps after `/clear`.
  - Flag any confident all-Latin reading with no Vietnamese letters, not only regex hits. Today the regex misses most SFX words and all non-English lettering.

**Why it saves.** RapidOCR's bundled recogniser holds only 11 of 134 Vietnamese accented letters. Our caps come back as ASCII ("NHƯ THẾ NÀO?" → "NHU THE NAO?", "MẸ ƠI!" → "ME OI!") and match `\b(the|me|no|to)\b`. Measured on synthetic pages: 3-4 of 30 natural lines flagged; scaled to a ~300-line chapter, that is ~30-70 false entries. Each one Claude crops costs about 5.5k (70k/20 + a 600×600 crop of 484 tokens + 1.5k output).

**Saving.** 1-2k (if dismissed from the text alone) to 30k+ (if each is cropped, as `super-lovers-finish.md` requires). Phase 2 only. To measure: diff the scan's output on `NNN.png` against `NNN_art.png` for Kimigazer Ch.1.

**Quality risk.** (a)+(b): neutral to positive, because fewer false hits make real leftovers easier to see. (c): positive for SFX coverage, but it adds hits, which is why the accepted-spots file then pays off.

**Rule conflicts.** None. It changes a documented operator step: update `docs/photoshop.md` and the three memory files that name the command.

**Effort.** (a)+(b) small; (c) medium.

**Integration.**
- `scripts/leftover_scan.py` (`--glob` at line 35, regex 25-29, filter at 53).
- Art file saved at `comic/photoshop/driver.py:222`, checked at 236-238.
- Chapters built before the art file existed need `comic ps art` first.

## 7. SFX gate: carry the ocr_review verdict forward, remember its own answers, list `kind=sfx`
**What.**
- **(a)** Add an optional closed-vocabulary `drop` reason to `OcrReviewItemResponse`: `art | front_matter | watermark | sfx`. Record drops in `dropped.json` via `write_json`.
- **(b)** `open_request` hides only `art` and `front_matter` drops. It always lists `sfx`, `watermark` and drops with no reason, and it skips ids already answered or `declined`. Keep `--all`, and print a count of hidden ids per page.
- **(c)** List regions the classifier labelled `kind=sfx`. Today they are never offered (`sfx.py:270-284`), and naming one fails with "no such dropped region -- give it a bbox" (`sfx.py:653-655`).

**Why it saves.** Every SFX-gate crop is a region Claude already saw and dropped at ocr_review, and the policy text (`sfx.py:308-314`) makes Claude decline art and logos a second time. A re-run of `comic ps sfx` re-lists everything, including what was already answered.

**Saving.** About 2.5k per crop not re-read (a 396×296 crop = 15 × 11 = 165 tokens + 250 for output + ~2k for the turn). At 5-10 such crops on paged English scanlations: ~10-25k. The one webtoon measured had 21, about 52k, but that came from a mis-declared Korean run. A resumed pass that skips 60 answered regions saves ~150k. Part (c) adds crops, so its net token effect may be negative; its value is quality.

**Quality risk.** Low, not zero. An SFX that Claude labels `art` is hidden, and `leftover_scan` would not catch most SFX words (see #6). The mitigations above cover this.

**Rule conflicts.** None. The `drop` reason is a closed vocabulary validated in `schemas.py`, and watermarks stay `erase_only` by default.

**Effort.** Medium.

**Integration.**
- `handoff/schemas.py:58-65`
- `stages.py` drop branch, around 661-666
- `photoshop/sfx.py`: `SfxResponse` 201-203, `dropped_regions` 259-284, `open_request` 287-319, `sfx_layers` boxes around 611
- `photoshop/cli.py: sfx`

## 8. Trim each ocr_review contact sheet to the width it uses (merged from two streams)
**What.** In `contact_sheet`, allocate the canvas at the widest row actually used, not at a fixed `SHEET_WIDTH = 1500` (`analyze.py:170`, `222`). Include each tile's label width (`cv2.getTextSize`, about 75-80 px for `p004_r11`), or a narrow tile's id gets clipped.

**Why it saves.** Blank canvas is billed as image patches.

**Saving.** A one-row sheet is 1500×296 → 54 × 11 = 594 tokens. With one 460 px tile it trims to 474 px → 17 × 11 = 187 tokens (−407). Two tiles: −220. Full rows: about 0. For ~25 sheets holding 1-2 crops, about 7.5-12k fresh plus cache re-sends ≈ 10-15k. Not measured.

**Quality risk.** None: the same tiles and labels, at the same pixels.

**Rule conflicts.** None. Packing several pages onto one sheet needs the operator's decision (it is next to the rejected "many crops at once"). If ever done, keep every side ≤2000 px (at most 6 rows), or long conversations hit the many-image limit.

**Effort.** Small (about 5 lines).

## 9. Give `en` its own recogniser (PP-OCRv5 English mobile, ONNX), and keep the detector
**What.** Add an `EnglishOcrProvider` that copies `KoreanOcrProvider.__init__` but **keeps** the detector, and remove `en` from `RapidOcrProvider.languages` (`registry.py:362`). Bench it first:
- add `ocr_bench.py --keep-spaces` (today `_DROP` deletes spaces);
- count flagged readings and confident-but-wrong readings;
- diff old and new readings on regions that were never reviewed.

**Why it saves.** Every chapter now in the queue is `--language en`, read today by the bundled Chinese weights. Fused words and low confidence send real lines to ocr_review. Paddle's own table gives en_PP-OCRv5_mobile_rec 85.25 against 70.39 for en_PP-OCRv4, at 7.5 MB, and says the new model improves on omitted spaces.

**Saving.** If the bench shows the assumed 1/3 to 2/3 fewer flags on real text: 10-20 fewer crops × ~850 (tile ~300 + output ~400 + cache ~150) ≈ 8.5-17k per English chapter. Over the ~130-160 English chapters left, about 1.1-2.7M. Mi Imitador is finished, so 0 there. No English baseline count exists yet.

**Quality risk.** Must be measured before switching:
- `LOW_CONFIDENCE = 0.72` was calibrated on the old engine.
- Korean SFX in scanlations will read as Latin garbage.
- PP-OCRv5 may still have space problems.

**Required details (reviewer):**
- distinct provider name (`rapidocr_en`), registered in `available()`;
- `accepts_vertical_fallback = False`;
- keep the old provider behind an env switch during the A/B (otherwise missing files raise `OcrError` and English chapters hard-fail);
- check that the model's class count equals dict lines + 2.

Already-analysed chapters need OCR re-run with `--force`; reviewed regions stay protected.

**Rule conflicts.** None: a narrow local recogniser (Apache-2.0, offline, same CPU budget). **Effort.** Small.

**Evidence.**
- https://raw.githubusercontent.com/PaddlePaddle/PaddleOCR/main/docs/version3.x/module_usage/text_recognition.en.md
- https://huggingface.co/paddlepaddle/en_pp-ocrv5_mobile_rec
- https://github.com/ogkalu2/comic-translate
- https://raw.githubusercontent.com/ogkalu2/comic-translate/main/modules/utils/download.py
- https://www.infoq.com/news/2025/09/baidu-pp-ocrv5

## 10. Fused-word readings: auto-space them, or stop flagging fused-only readings
**What.** Do this after #9's bench, since PP-OCRv5 may remove most fused readings. Two options:
- **(a)** Split only the fused alphabetic token with symspellpy `word_segmentation` or wordninja. Accept the split only when the reading is otherwise unflagged and the joined letters equal the original. Record the provider as `rapidocr+ws`.
- **(b) Cheaper.** Measure on reviewed chapters whether fused-only regions actually contain misreads. If they rarely do, stop flagging fused-only readings.

**Why it saves.** 16 of 80 English readings carried a 15-17-letter token. Each one goes to ocr_review only to have its spaces put back.

**Saving.** About 20-25k per English chapter: ~20 tiles, transcriptions, request items, and ~6 pages whose only flag it was × (594 + 1.8k). It rests on one chapter's 16/80 rate and is unmeasured.

**Quality risk.** Low, not zero:
- the fused flag is also the eye check for confident misreads inside those tokens;
- wordninja drops punctuation (its split regex) and will split character names;
- its vocabulary includes fragments, so "every word is in the list" is a weak guard;
- its last release was 2019.

**Rule conflicts.** Yes: this reverses `registry.py`'s documented "never guess where a space belongs". **Effort.** Small-medium.

**Integration.** `ocr/registry.py:215-234`; `stages.py:349-355` and `457-462`.

**Evidence.**
- https://github.com/keredson/wordninja
- symspellpy (MIT)

## 11. `retranslate_shorter` requests carry the current line, speaker, addressee and voice
**What.** When `limits` is set, fill new optional `TranslateItem` fields (current text, speaker, addressee, voice) from `translation.json`. In `apply_translation`, carry omitted fields over from the previous entry. Use pydantic's `model_fields_set`, so Claude can still clear a field on purpose.

**Why it saves.** Under "one gate per conversation", Claude has to open the whole `translation.json` or translate from scratch.

**Saving.** About 14k for the file (200 entries × 70) + ~7k riding in cache ≈ 21k per retranslate round. That happens 0-1 times per chapter, so the expected value is lower. The main value is correctness: today a shortened answer silently erases voice, which changes the face at render (`plan.with_voices`), and can erase speaker and addressee.

**Quality risk.** Positive: it starts from the approved wording and keeps the pinned pronoun pair. **Rule conflicts.** None. **Effort.** Small.

**Integration.** `handoff/schemas.py:76-95`; `translation/gate.py:112-148` and `289-315`.

## 12. Second-opinion detector (RT-DETR-v2) to auto-drop flagged artwork that is not in a bubble
**What.** For regions about to be flagged only for an empty or low-confidence reading, run comic-translate's RT-DETR detector. A region is removed exactly like an agent's `""` only when all of these hold:
- not dialogue and not in a bubble;
- not truncated or fused;
- not reviewed;
- no RT-DETR text box covers it.

The auto-dropped ids are listed as text only in the request.

**Why it saves.** On the one webtoon measured, 14 of 56 flagged crops were ornaments or effect strokes.

**Saving.** A ceiling of about 9-12k per webtoon chapter: a 4-row sheet of 2,322 tokens + 600-900 for other crops + 4.2k output + 1.5k cache. On the English path, 10 of 11 ornaments still read empty, and effect strokes are unmeasured. Zero on paged manga. On the remaining ~120 webtoon chapters, at most about 1M, realistically less.

**Quality risk.** Not neutral: a real line dropped here is never translated. Enable only if a shadow bench shows zero kept regions would have been dropped across all reviewed chapters. `comic ps sfx` shows every dropped region later, but pages published earlier through the pipeline would carry the English until then.

**Required details (reviewer):**
- force `CPUExecutionProvider`, not DirectML (int8 on Iris Xe is unverified) and keep `ort_options()` for the thread cap;
- the default `detector-v4-s_int8.onnx` file's architecture and training are undocumented;
- the model card licence could not be read directly;
- do not filter inside `extract_regions`: ids are re-derived by index (`sfx.py:259-284`).

**Rule conflicts.** Yes: it narrows "where judgment is needed the pipeline stops and asks". **Effort.** Medium.

**Integration.** `vision/detector.py` beside `_session` (115-132); `schemas.py` around 184-194; `stages.py:349-355`, `457-462`, `663-666`.

**Evidence.**
- https://huggingface.co/ogkalu/comic-text-and-bubble-detector
- https://raw.githubusercontent.com/ogkalu2/comic-translate/main/modules/detection/rtdetr_v2_onnx.py
- https://raw.githubusercontent.com/ogkalu2/comic-translate/main/modules/utils/download.py

## 13. Review each repeated reading (watermark) once
**What.** Use the reviewer's safer variant:
- keep the per-page sheets;
- state the long "repeated on N pages" reason once in the instructions, with a short code per item;
- accept one group answer only when the normalised reading looks like a URL, domain or "scans" credit;
- answer everything else per id.

**Why it saves.** One site watermark was flagged on 11 pages, each with a ~50-token reason and its own `""` answer.

**Saving.** About 6-10k per chapter that has a watermark, net of any extra sheet. Zero otherwise.

**Quality risk.** Low with the URL-shaped guard. A blanket group answer would risk dropping repeated real lines ("...", "WHAT?!").

**Rule conflicts.** Grouping: none. Auto-dropping across chapters needs the operator: the `WATERMARK_PAGES` comment says "the agent decides".

**Effort.** Small-medium.

**Integration.** `stages.py:53-78`, `464-509`, `549-583`, `644-679`.

## 14. `comic analyze --force` must not wipe reviewed work
**What.** In `detect_one`, merge with the previous analysis when the source page (hash) and the mask are unchanged:
- copy the agent's reading for the same id with an identical bbox;
- re-drop an id that is absent from the old analysis, when the mask is byte-identical.

Simpler alternative: make `--force` on `analyze` re-run OCR only, and add `--redetect`.

**Why it saves.** Today a forced re-detect sends the whole reviewed chapter back to ocr_review (`stages.py:194-196` writes with no merge). That defeats `ocr_one`'s own guard (316-321).

**Saving.** About 85k per occurrence: 30 sheets × 800 = 24k, + 30 turns × 1.8k = 54k, + 60 × 25 × 5 = 7.5k. How often it happens is unknown; the flag is used (`stages.py:460-463`).

**Quality risk.** Neutral: only identical crops of identical pages inherit; anything changed is reviewed again. **Rule conflicts.** None. **Effort.** Small-medium.

## 15. Shorter validation errors and progress output (merged with the `--quiet` idea)
**What.**
- **Schema failures.** Print counts grouped by (error type, field path), the first 3 errors without URLs, and write the full dump to `handoff/<gate>_errors.txt`.
- **Progress.** Print per-page lines only for pages with a problem, plus one summary line. Keep warnings and the GATE block.
- **ocr_review reasons.** Move only the long repeated reason sentences into the request instructions, with a short code per item.
- **Keep:** the `tiny_font` advice next to each finding (it was added after a measured mistake), and `setup_console()`.

**Saving.**
- Re-measured: 200 items with one wrong key gives 400 errors, 88,023 chars, about 22k tokens, paid when Claude opens the saved output. That is rare.
- Progress lines: about 2-6k per chapter, steady.

**Quality risk.** None, as long as the full text stays in a file. **Effort.** Small.

**Integration.**
- `stages.py:641-642`, `translation/gate.py:263-264`
- `cli/app.py` around 1069-1075 and the progress lambdas (around 791, 801, 1013, 1023, 1194, 1200)
- `photoshop/cli.py:184`
- `stages.py: _review_reason` (486-518)

## 16. Leftover-lettering pre-check inside `comic render`
**What.**
- **Start cheap, no model.** In plain (flat-fill) bubbles, any dark component left in the cleaned area that is outside the erase mask and does not touch the area's edge is leftover lettering or punctuation. This catches the logged quote-mark and cut-off-word cases at millisecond cost.
- **Then, optionally.** Run comic-text-detector on 512 px tiles of the cleaned page:
  - only when DirectML is active; on CPU it is ~3 minutes per page, which breaks the 90% CPU rule;
  - emit only `leftover_text` inside translated areas, with a suggested `expand_mask N`, capped at about 10-12 px;
  - send unmarked findings to the `comic ps sfx` step, not to visual_qa, where SFX would flood it.

**Saving.** About 18-20k per avoided visual_qa round, now that rounds 2+ show only fixed pages: 2 images × 1,395 + ~2k request + 6 turns × 25k/20 + 1.5k output × 5. Before wiring it in, measure recall and false alarms against `leftover_scan` on a finished chapter.

**Quality risk.** Neutral for pixels; the risk is false alarms. **Rule conflicts.** None: an existing local model and existing actions. **Effort.** Small (cheap variant) to medium.

## 17. Fix how LaMa is fed
**What.**
- **Now (bug fixes).** Feed RGB: `load_page` returns RGB, but `clean.py:189` converts BGR→RGB, so the model gets BGR. This matters on the colour webtoons in progress. Also erase every glyph pixel of the region in the inpaint tier: today `hole` is clipped to box ±32 px (`clean.py:313-318`), while the flat tier erases them all. Re-check one Dasadanan page afterwards.
- **Behind a new `scripts/inpaint_bench.py`.** A native-resolution 512 window centred on the hole, and context taken from `cleaned` instead of the original.

**Saving.** About 0-5k in Claude tokens: a smudge has no remedy in the closed vocabulary, so it rarely costs a round. The real gain is fewer smudges for the operator to fix by hand.

**Quality risk.** Expected positive. Changing `clean.py` re-renders every page once (the render fingerprint includes the code).

**Corrections (reviewer).**
- Super Lovers is `clean: auto`, not `lama`. No profile uses `clean: lama`.
- The "thin strokes lost" concern does not hold at the scales that occur.
- Zeroing the hole before inference is likely redundant (the exported wrapper does it).

**Effort.** Small for the fixes; medium for the bench.

**Evidence.**
- https://raw.githubusercontent.com/Sanster/IOPaint/main/iopaint/model/base.py
- https://raw.githubusercontent.com/ogkalu2/comic-translate/main/modules/inpainting/schema.py

## 18. Optional manga-finetuned LaMa per series, adopted only on a bench win
**What.** `lama-manga-dynamic.onnx` (ogkalu, the dreMaz AnimeMangaInpainting finetune; sha256 `de31ffa5…315f9`), as an optional `fetch_models.py` entry and a `fonts.yaml: clean_model: manga` switch.

**Saving.** About 0-10k, indirect, like #17. It is a quality experiment.

**Quality risk.** Unknown until benched; it could do worse on colour webtoons.

**Before adopting:** a licence check of the model cards (unreadable from here), an OpenVINO CPU compile test on the laptop, and whether the export zeroes the hole.

**Rule conflicts.** Operator decision per series. **Effort.** Medium.

**Evidence.**
- https://raw.githubusercontent.com/ogkalu2/comic-translate/main/modules/utils/download.py
- https://raw.githubusercontent.com/Sanster/IOPaint/main/iopaint/model/lama.py

## 19. Switch High to Medium with `/effort`, never with `/model`
**What.** At the pause after chapters 2-3:
1. Claude stops at a chapter boundary with state saved.
2. The operator runs `/clear`, then `/effort medium` (Enter saves it as the default; `s` applies it to this session only).
3. The operator checks with `/effort status` and says "continue".

At Medium, `ultrathink` in a prompt deepens one hard turn (an ambiguous speaker or pronoun pair) without changing the level. Make sure no `CLAUDE_CODE_EFFORT_LEVEL` environment variable is set (per the model-config docs; not re-checked).

**Why.** Docs, verified: on Opus 5.5 with a subscription, "changing effort keeps the cache". `/model`, or a skill or command naming another model, re-reads the whole conversation uncached.

**Saving.** 0 if done after `/clear`. It avoids a one-off of about 200k (100k context × 2) if someone switches with `/model` mid-conversation.

**Note.** The "never switch model/effort mid-conversation" line was already removed in the last commit (e58f829). What is still missing is step 2 of the workflow in `token-savings.md` naming `/effort medium`.

**Evidence.**
- https://code.claude.com/docs/en/prompt-caching#changing-effort-level
- https://code.claude.com/docs/en/prompt-caching#switching-models

## 20. Never Monitor or poll `comic` runs; keep the 1-hour cache if usage credits are on
**What.** Run `comic` commands in the foreground, or once with `run_in_background`, and wait for the exit notice. If the operator turns on usage credits, set `"promptCacheTtl": "1h"`. Do not set `FORCE_PROMPT_CACHING_5M`.

**Why.** Every Monitor event is a new turn carrying the full context. On a subscription the main conversation gets a 1-hour cache, but it drops to 5 minutes on usage credits, so a render longer than 5 minutes re-writes the whole context.

**Saving.** 0 if not done today. It avoids ~245k per monitored 30-page analyze (65 lines × (60k × 0.05 + 150 × 5)) and ~125k per 5-minute-TTL miss at a 100k context.

**Effort.** None (a workflow line in CLAUDE.md).

**Evidence.**
- https://code.claude.com/docs/en/prompt-caching#cache-lifetime (verified)
- https://code.claude.com/docs/en/costs#why-usage-climbs-in-a-long-session

## 21. Gate images: lossless WebP, never over 2000 px
**What.**
- Write gate copies (crops, overlays, the visual_qa rendered page and original) as lossless WebP: `cv2.IMWRITE_WEBP_QUALITY` above 100 means lossless. Keep the `cleaned/` and `translated/` archives as they are.
- Show pages taller than 2000 px as two overlapping halves at the gate.
- Do **not** lower `strip.MAX_HEIGHT`: a lower value means more forced cuts through artwork.

**Why.**
- Docs: once a request holds more than 20 images, images over 2000 px fail ("many-image requests") unless resized.
- Binary strings (not in the docs): Claude Code resizes to a 2000×2000 box and re-encodes files over 3.75 MB, as palette PNG and then JPEG.
- So 2600 px webtoon pages are already seen downscaled in long gate conversations, which works against the operator's "no downscaling".
- Lossless WebP measured 42-61% of PNG bytes, pixel-identical. That keeps pages under the re-encode threshold and roughly doubles the pages per batch in #1.

**Saving.** About 0 tokens by itself. A halved 720×2600 page costs about +1k tokens (2,444 vs 1,440).

**Quality risk.** Positive. **Rule conflicts.** None: it is the opposite of the rejected downscaling.

**To verify first:** that the operator's Claude Code Read accepts `.webp`. The API supports WebP.

**Effort.** Small.

**Integration.** `analyze.py:126-133 write_crop` (`cv2.imencode('.png')`, verified); `qa/visual.py` around 404-406 (copies the rendered PNG bytes); `strip.py:70-72`.

**Evidence.**
- https://platform.claude.com/docs/en/build-with-claude/vision#request-limits (verified, WebP supported)
- https://code.claude.com/docs/en/errors#image-was-too-large
- https://docs.opencv.org/3.4.20/d8/d6a/group__imgcodecs__flags.html

---

## Records kept so nobody re-evaluates them (no saving)
- **Models checked and not recommended:**
  - MAGI v1-v3: "academic research purposes only", CUDA.
  - ogkalu YOLOv8m: AGPL per Ultralytics, superseded.
  - Surya: OpenRAIL-M licence with a revenue cap, and a VLM (second LLM).
  - PaddleOCR-VL: LLM decoder.
  - Florence-2: generative VLM, best on GPU.
  - EasyOCR: no evidence it beats PP-OCRv5.
  - docTR/OnnxTR: kept as the fallback if #9 fails its bench.
- **Do not move `comic` commands into a cheaper subagent** (unreviewed, docs-consistent). Its own prompt plus CLAUDE.md at the 5-minute write rate costs ~13-20k per spawn, against ~2-5k saved: net −10 to −15k per spawn. Warnings get summarised away, and Sonnet/Haiku were error-prone for this operator.

## Dropped by the review
- **Show only changed pages after `comic ps build`.** The full-page review is the only backstop for English no gate listed: `kind=sfx` regions have no item, and `leftover_scan` misses most SFX words. It is the same trade the operator already rejected. Revisit only after #7(c) and #6(c), and with the operator's OK.
- **Baberu OCR for Chinese.** 0 saving for the queue (all English); the zh/en accuracy is unconfirmed; it would silently disable the truncation guard (`registry.py:154-155`).
- **Smudge and colour-speck pre-check.** A flagged smudge has no closed-vocabulary remedy, so it cannot save a round. Colour specks are already fixed by `_is_grey`, and the "pale blob" evidence came from Photoshop SFX fills. The thresholds are uncalibrated.