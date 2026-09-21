"""Score OCR against transcriptions an agent verified by eye.

Every reading corrected at the `ocr_review` gate is ground truth: someone
looked at the crop and typed what was printed. That makes any chapter that has
been through the gate a free evaluation set, and it is the only honest way to
judge an OCR change -- "it looks better on this bubble" is how thresholds get
tuned into the ditch.

    python scripts/ocr_bench.py --series conan --chapter 49
    python scripts/ocr_bench.py --series conan --chapter 49 --verified-only

Reports character similarity and exact-match count. Punctuation is normalised
away first: the engine writes `!` where the page prints `！`, and that is not a
difference worth optimising against.
"""

from __future__ import annotations

import argparse
from difflib import SequenceMatcher

from comic.console import setup_console
from comic.ocr.registry import read_region
from comic.paths import ChapterPaths
from comic.stages import analysis_for
from comic.state.store import ChapterStore
from comic.vision.analyze import crop_region
from comic.vision.detector import load_page

#: Differences that are not mistakes: width variants, and punctuation the
#: engine renders in ASCII where the page prints it full-width.
_FOLD = str.maketrans(
    {
        "！": "!", "？": "?", "，": ",", "、": ",", "。": ".", "：": ":",
        "；": ";", "（": "(", "）": ")", "　": " ",
    }
)
_DROP = "…·—–\"'“”‘’ \t"


def fold(text: str) -> str:
    text = text.translate(_FOLD)
    return "".join(c for c in text if c not in _DROP).strip()


def main() -> int:
    setup_console()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--series", required=True)
    parser.add_argument("--chapter", required=True)
    parser.add_argument(
        "--verified-only",
        action="store_true",
        help="Score only regions an agent actually corrected.",
    )
    args = parser.parse_args()

    paths = ChapterPaths(args.series, args.chapter)
    state = ChapterStore(paths).load()

    rows: list[tuple[str, float, str, str]] = []
    for record in state.pages:
        analysis = analysis_for(paths, record.page)
        if analysis is None:
            continue
        image = None
        for region in analysis.regions:
            if not region.translatable or not region.source_text.strip():
                continue
            if args.verified_only and not region.ocr_reviewed:
                continue
            if image is None:
                image = load_page(paths.original / record.filename)
            outcome = read_region(
                crop_region(image, region),
                region.source_language or state.source_language,
                vertical=region.is_vertical,
            )
            truth = fold(region.source_text)
            got = fold(outcome.text)
            rows.append(
                (region.id, SequenceMatcher(None, got, truth).ratio(), got, truth)
            )

    if not rows:
        print("nothing to score; has this chapter been through the ocr_review gate?")
        return 1

    rows.sort(key=lambda row: row[1])
    exact = sum(1 for _id, ratio, got, truth in rows if got == truth)
    mean = sum(row[1] for row in rows) / len(rows)

    print(f"{len(rows)} region(s): mean character similarity {mean:.3f}, "
          f"{exact} exact ({exact / len(rows):.0%})\n")
    print("worst readings:")
    for region_id, ratio, got, truth in rows[:10]:
        print(f"  {ratio:.2f} {region_id}")
        print(f"       engine: {got}")
        print(f"       printed: {truth}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
