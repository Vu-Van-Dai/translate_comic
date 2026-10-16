"""OCR providers, and choosing between them.

Each provider is a narrow tool that recognises text and nothing else. They are
loaded lazily and cached, because initialising one costs seconds while reading a
crop costs milliseconds.

Adding or replacing an engine means adding a class here; no other stage changes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from functools import lru_cache
from typing import Protocol

import numpy as np

log = logging.getLogger(__name__)

#: Below this, a reading is not trusted and the region goes to the agent.
LOW_CONFIDENCE = 0.72

#: OCR reads a small crop poorly; enlarging it first is measurably better.
UPSCALE_BELOW_HEIGHT = 90
UPSCALE_FACTOR = 3


class OcrError(RuntimeError):
    """Raised when no provider can serve a language."""


@dataclass(frozen=True)
class OcrOutcome:
    text: str
    confidence: float
    provider: str
    #: (x1, y1, x2, y2) per run, in the coordinates of the crop handed in.
    #: Empty when the engine does not report geometry, which means "unknown",
    #: not "nothing" -- see ``unread_mask_fraction``.
    boxes: tuple[tuple[float, float, float, float], ...] = ()

    @property
    def suspicious(self) -> bool:
        """Whether this reading should be checked by the agent.

        Low confidence is the obvious case. The others are shapes that engines
        produce when they have essentially failed but still return something: an
        empty string, or a single stray character from a region big enough to
        hold a sentence.
        """
        if not self.text.strip():
            return True
        return self.confidence < LOW_CONFIDENCE


class OcrProvider(Protocol):
    name: str
    languages: frozenset[str]
    supports_vertical: bool
    #: Whether manga-ocr is an acceptable last resort when this engine reads
    #: a vertical region as empty. It shares a script with Chinese, so a weak
    #: candidate there beats a blank; it has no Hangul at all, so for Korean
    #: its output cannot be a reading, only noise shaped like one.
    accepts_vertical_fallback: bool

    #: Weight files this provider needs in ``models/``, beyond its package.
    #: An engine whose package imports but whose weights are absent is not
    #: available, and reporting it as available sends the operator looking in
    #: the wrong place when a chapter fails.
    requires_files: tuple[str, ...]

    def read(
        self,
        crop: np.ndarray,
        vertical: bool = False,
        mask: np.ndarray | None = None,
    ) -> OcrOutcome: ...


#: Above this height:width ratio a detected run is a column, not a line.
COLUMN_ASPECT = 1.5

#: How much two runs must overlap across the text flow to count as the same
#: column (or line). Measured: runs split inside one column overlap 0.9+;
#: neighbouring columns reach only 0.1-0.5 even with loosely drawn boxes.
SAME_TRACK_OVERLAP = 0.6


@dataclass(frozen=True)
class _Chunk:
    """One run of text an engine found, with enough geometry to place it."""

    cx: float
    cy: float
    width: float
    height: float
    text: str
    score: float

    @property
    def is_column(self) -> bool:
        return self.height > self.width * COLUMN_ASPECT


def _chunks(result: list) -> list[_Chunk]:
    """RapidOCR's ``(box, text, score)`` triples, as measurable chunks."""
    out: list[_Chunk] = []
    for box, text, score in result:
        xs = [float(p[0]) for p in box]
        ys = [float(p[1]) for p in box]
        out.append(
            _Chunk(
                cx=sum(xs) / len(xs),
                cy=sum(ys) / len(ys),
                width=max(xs) - min(xs),
                height=max(ys) - min(ys),
                text=text,
                score=float(score) if score is not None else 0.0,
            )
        )
    return out


#: Above this share of unread text pixels a reading is treated as incomplete.
#:
#: Measured over 36 regions of the Conan chapter, where this ranks the
#: readings almost exactly by how much they lost. Everything at 0.22 and up was
#: genuinely truncated -- `子去玩吧` at 0.79 is one of a bubble's three columns,
#: and even the single-glyph `哇` at 0.22 had dropped its `!!`. Everything at
#: 0.08 and below was complete. The mask is dilated, so a little spill past the
#: OCR boxes is normal and the floor is not zero.
#:
#: Set low on purpose. This opens a review gate, so a false stop costs one crop
#: read, while a miss puts a silently truncated line into the translation.
MAX_UNREAD_MASK = 0.20


def unread_mask_fraction(
    mask: np.ndarray, boxes: tuple[tuple[float, float, float, float], ...]
) -> float:
    """How much of the detector's text is not covered by any OCR run.

    This catches the failure confidence cannot: an engine that finds two of a
    bubble's three columns reads those two *perfectly* and reports 1.00, so
    nothing looks wrong and a line silently loses a third of its words. Our own
    segmentation mask knows where the text is, so comparing the two turns a
    silent truncation into a gate stop.

    ``mask`` is the region's slice of the page mask; ``boxes`` are in the same
    coordinates. No boxes means the engine reports no geometry, which is not
    evidence of anything, so it reads as fully covered.
    """
    if not boxes or mask.size == 0:
        return 0.0
    text = mask > 0
    total = int(text.sum())
    if total == 0:
        return 0.0

    height, width = text.shape[:2]
    covered = np.zeros((height, width), dtype=bool)
    for x1, y1, x2, y2 in boxes:
        left = max(0, int(round(x1)))
        top = max(0, int(round(y1)))
        right = min(width, int(round(x2)) + 1)
        bottom = min(height, int(round(y2)) + 1)
        if right > left and bottom > top:
            covered[top:bottom, left:right] = True
    return float((text & ~covered).sum()) / total


#: Which scripts each language is actually written in. Used to notice that a
#: chapter is not in the language its adapter claims.
LANGUAGE_SCRIPTS: dict[str, frozenset[str]] = {
    "ko": frozenset({"hangul"}),
    "kor": frozenset({"hangul"}),
    "korean": frozenset({"hangul"}),
    "ja": frozenset({"kana", "han"}),
    "jp": frozenset({"kana", "han"}),
    "japanese": frozenset({"kana", "han"}),
    "zh": frozenset({"han"}),
    "ch": frozenset({"han"}),
    "zh-cn": frozenset({"han"}),
    "zh-tw": frozenset({"han"}),
    "en": frozenset({"latin"}),
}

#: Share of readings that must disagree before the chapter is called wrong.
#: High, because one English sign in a Korean comic is ordinary; a whole
#: chapter of them is a misconfigured adapter.
MISMATCH_SHARE = 0.6


def dominant_script(text: str) -> str | None:
    """Which script most of ``text`` is written in, ignoring punctuation."""
    counts: dict[str, int] = {}
    for char in text:
        if "가" <= char <= "힣" or "ᄀ" <= char <= "ᇿ":
            key = "hangul"
        elif "぀" <= char <= "ヿ":
            key = "kana"
        elif "一" <= char <= "鿿" or "㐀" <= char <= "䶿":
            key = "han"
        elif char.isascii() and char.isalpha():
            key = "latin"
        else:
            continue
        counts[key] = counts.get(key, 0) + 1
    if not counts:
        return None
    return max(counts, key=lambda k: counts[k])


#: Longest plausible word in Latin comic lettering. Above this, the recogniser
#: has fused words together.
#:
#: Measured on a real English scanlation: after runs are joined correctly, 64
#: of 80 readings have sane word lengths and 16 carry a token of 15-17
#: characters. Those are not long words, they are two or three short ones run
#: together -- the recogniser drops spaces *inside* a run on stylised
#: lettering, which no amount of joining can repair.
#:
#: So this does not try to fix them; it sends them to the gate, in the same
#: spirit as ``unread_mask_fraction``: notice the reading is degraded, let the
#: agent supply the words, never guess where a space belongs.
MAX_LATIN_WORD = 14


def reading_looks_fused(text: str) -> bool:
    """Whether a Latin reading has words run together."""
    if dominant_script(text) != "latin":
        return False
    return any(len(word) > MAX_LATIN_WORD for word in text.split())


def join_runs(parts: list[str]) -> str:
    """Join OCR runs with the separator the script actually requires.

    An engine returns *runs*, not sentences, and how they go back together
    depends on the writing system. CJK sets without spaces, so ``"".join`` is
    right -- and being right there is why it went unnoticed, since the engine
    was measured on Chinese first.

    Latin scripts put a space between words and each run is a word or short
    phrase, so joining those with nothing yields run-together text: measured on
    a real English scanlation, a whole line of dialogue came back as one
    unbroken token. That cannot be translated, cannot be wrapped, and the
    length checks read it as a single enormous word.

    Decided from the text itself rather than from the declared language, so it
    stays right for a Latin caption inside a Chinese page.
    """
    kept = [p.strip() for p in parts if p and p.strip()]
    if not kept:
        return ""
    if dominant_script("".join(kept)) == "latin":
        return " ".join(kept)
    return "".join(kept)


def language_mismatch(texts: list[str], language: str) -> str | None:
    """Whether a chapter is written in a different script than declared.

    A wrong-language engine produces *confident nonsense*, and the one check
    that would catch it -- ``provider_for`` refusing to substitute an engine --
    never fires when the **adapter** is what is wrong: the pipeline is told
    `ko`, loads a Korean recogniser, and points it at English lettering.

    Measured on a real chapter: yaoiscan publishes English scanlations of
    Korean manhwa, so every bubble is English while the SFX stay Hangul. The
    adapter said `ko` and nothing downstream noticed.

    Returns a message naming what was actually found, or None.
    """
    expected = LANGUAGE_SCRIPTS.get((language or "").lower())
    if not expected:
        return None
    found = [s for s in (dominant_script(t) for t in texts if t.strip()) if s]
    if len(found) < 5:
        return None
    wrong = [s for s in found if s not in expected]
    share = len(wrong) / len(found)
    if share < MISMATCH_SHARE:
        return None
    actual: dict[str, int] = {}
    for script in wrong:
        actual[script] = actual.get(script, 0) + 1
    winner = max(actual, key=lambda k: actual[k])
    return (
        f"{share:.0%} of readings are {winner}, not the {sorted(expected)[0]} "
        f"that --language {language!r} implies. The source language is probably "
        f"wrong -- check the site adapter; OCR has been using the wrong engine."
    )


def _reads_vertically(chunks: list[_Chunk], hint: bool) -> bool:
    """Whether this block is set in columns, decided by what was detected.

    The shape of the *region* is a poor signal and was measurably wrong here: a
    bubble holding four short columns is wider than it is tall, so an aspect
    test calls it horizontal and the columns come back reversed. The shape of
    the detected runs is direct evidence, so a strict majority of tall runs
    decides it, and the region hint only breaks an exact tie.
    """
    columns = sum(1 for c in chunks if c.is_column)
    if columns * 2 == len(chunks):
        return hint
    return columns * 2 > len(chunks)


def _in_reading_order(chunks: list[_Chunk], vertical: bool) -> list[_Chunk]:
    """Sort detected runs into the order a person reads them.

    Engines return their own order, which is roughly top-to-bottom. For CJK set
    in columns that is exactly backwards -- columns run right to left -- so
    joining raw output scrambles the sentence while every individual run looks
    perfectly correct, which is the hard kind of wrong to notice.
    """
    if len(chunks) < 2:
        return list(chunks)

    if vertical:
        lo, hi = (lambda c: c.cx - c.width / 2), (lambda c: c.cx + c.width / 2)
        across, along = (lambda c: -c.cx), (lambda c: c.cy)
    else:
        lo, hi = (lambda c: c.cy - c.height / 2), (lambda c: c.cy + c.height / 2)
        across, along = (lambda c: c.cy), (lambda c: c.cx)

    def shares_track(a: _Chunk, b: _Chunk) -> bool:
        """Whether two runs sit in the same column (or on the same line).

        Measured overlap, not a distance tolerance: a tolerance scaled to the
        box size is about one column wide, so it merges *neighbouring* columns
        and then orders them by height, which reverses a sentence. Runs the
        detector split inside one column overlap almost completely; adjacent
        columns barely touch even when their boxes are drawn loosely.
        """
        overlap = min(hi(a), hi(b)) - max(lo(a), lo(b))
        widest = max(hi(a) - lo(a), hi(b) - lo(b))
        return widest > 0 and overlap / widest > SAME_TRACK_OVERLAP

    groups: list[list[_Chunk]] = []
    for chunk in sorted(chunks, key=across):
        if groups and shares_track(chunk, groups[-1][0]):
            groups[-1].append(chunk)
        else:
            groups.append([chunk])
    return [c for group in groups for c in sorted(group, key=along)]


class RapidOcrProvider:
    """PP-OCR via onnxruntime. Good for Chinese and English.

    Its recogniser is horizontal-only, but its detector finds vertical columns
    and rotates them, so it reads CJK set in columns perfectly well -- it just
    hands them back in the wrong order. Ordering is this class's job, not the
    engine's.
    """

    name = "rapidocr"
    languages = frozenset({"zh", "ch", "en", "zh-cn", "zh-tw"})
    #: The *recogniser* cannot read a column as a unit; see the class docstring.
    supports_vertical = False
    #: manga-ocr is trained on Japanese but shares the Han script, and scored
    #: 0.62 against hand-transcribed vertical Chinese -- worth offering as a
    #: candidate the agent then checks, which is why it is gated behind
    #: VERTICAL_FALLBACK_CONFIDENCE rather than trusted.
    accepts_vertical_fallback = True
    #: Ships its own Chinese weights inside the package.
    requires_files = ()

    def __init__(self) -> None:
        from rapidocr_onnxruntime import RapidOCR

        # Deliberately CPU, whatever the detector uses. RapidOCR is handed one
        # small crop at a time, and per-call dispatch and transfer costs swamp
        # the compute: measured on this machine, a single trivial crop took
        # 1.40s on CPU and 71.12s through DirectML -- 51x *slower*. The GPU
        # win only exists for one big inference per page, which is what the
        # text detector does.
        from comic.cpu import threads_per_worker

        self._engine = RapidOCR(
            intra_op_num_threads=threads_per_worker(), inter_op_num_threads=1
        )

    def read(
        self,
        crop: np.ndarray,
        vertical: bool = False,
        mask: np.ndarray | None = None,
    ) -> OcrOutcome:
        result, _ = self._engine(crop)
        if not result:
            return OcrOutcome("", 0.0, self.name)
        chunks = _chunks(result)
        ordered = _in_reading_order(chunks, _reads_vertically(chunks, vertical))
        text = join_runs([c.text for c in ordered])
        scores = [c.score for c in chunks if c.score]
        confidence = float(np.mean(scores)) if scores else 0.0
        boxes = tuple(
            (
                c.cx - c.width / 2,
                c.cy - c.height / 2,
                c.cx + c.width / 2,
                c.cy + c.height / 2,
            )
            for c in chunks
        )
        return OcrOutcome(text.strip(), confidence, self.name, boxes)


#: Ink pixels a row needs, as a share of crop width, to be part of a line.
#: Low, because a Korean line is mostly whitespace between syllables.
LINE_INK = 0.015

#: Thinner than this a band is speckle, not a line of type.
MIN_LINE_HEIGHT = 5

#: Rows of clear space that separate two lines rather than two syllables.
LINE_GAP = 2


def _ink_rows(crop: np.ndarray) -> np.ndarray:
    """Per-row ink counts, whichever polarity the lettering is.

    Inverted panels -- white type on black -- are ordinary in comics, so the
    minority class is taken as ink rather than assuming dark-on-light. Getting
    this backwards finds bands in the gaps and reads the spaces.
    """
    import cv2

    gray = crop if crop.ndim == 2 else cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    _, binary = cv2.threshold(gray, 0, 1, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    if binary.sum() * 2 > binary.size:
        binary = 1 - binary
    return binary.sum(axis=1)


def line_bands(
    crop: np.ndarray, mask: np.ndarray | None = None
) -> list[tuple[int, int]]:
    """Split a crop into horizontal lines of type, top to bottom.

    Our own segmentation mask already decided where this region is; what is
    left is only to cut it into the single lines a recogniser expects. A
    projection does that without a second detector -- see ``KoreanOcrProvider``
    for why running one here is actively harmful.

    **Project the mask when there is one, not the crop.** Thresholding the crop
    counts every dark pixel, and the darkest thing in a speech bubble is the
    bubble's own outline: measured on a rendered Korean chapter, that added a
    thin band above and below every bubble, which the recogniser dutifully read
    as punctuation -- `괜찮아요?` came back as `. 괜찮아요?` and
    `나는 널 믿고 있었어` as `’. 나는 널 믿고 있었어 .`. The mask marks
    lettering and not outlines, so projecting it drops those bands entirely.
    """
    if crop.size == 0:
        return []
    if mask is not None and mask.shape[:2] == crop.shape[:2] and mask.any():
        rows = (mask > 0).reshape(mask.shape[0], -1).sum(axis=1)
    else:
        rows = _ink_rows(crop)
    inked = rows > max(1.0, LINE_INK * crop.shape[1])
    if not inked.any():
        return []

    padded = np.concatenate(([False], inked, [False]))
    edges = np.flatnonzero(padded[1:] != padded[:-1])
    bands = [(int(a), int(b)) for a, b in zip(edges[::2], edges[1::2])]

    merged: list[list[int]] = []
    for start, stop in bands:
        if merged and start - merged[-1][1] <= LINE_GAP:
            merged[-1][1] = stop
        else:
            merged.append([start, stop])
    return [(a, b) for a, b in merged if b - a >= MIN_LINE_HEIGHT]


class KoreanOcrProvider:
    """Korean via PP-OCRv5, driven one line at a time.

    The recogniser is only half of RapidOCR. The other half is a *Chinese*
    detector, and on Korean it is not merely unhelpful but destructive --
    measured on clean rendered lines at four sizes:

    | crop | detect + recognise | recognise only |
    |---|---|---|
    | 24px | 0.683 / 4 of 12 | **0.976 / 9 of 12** |
    | 32px | 0.538 / 3 of 12 | **0.969 / 8 of 12** |
    | 44px | 0.517 / 3 of 12 | **0.969 / 8 of 12** |
    | 60px | 0.529 / 1 of 12 | **0.988 / 11 of 12** |

    It splits syllables apart and appends fragments, so `정말 미안해요` came
    back as `름|안해요성들` through the detector and perfectly without it.

    Running it was redundant anyway: comic-text-detector has already decided
    where this region is, so the crop *is* the text. All that remains is
    cutting it into single lines, which a projection does (``line_bands``).

    Contrast Chinese, where the detector must stay: it is what finds vertical
    columns and rotates them, which is the only reason stacked CJK reads at
    all. So this is a Korean-specific choice, not a general one.
    """

    name = "rapidocr_ko"
    languages = frozenset({"ko", "kor", "korean"})
    #: Korean is set horizontally in comics; vertical Hangul is a rarity.
    supports_vertical = False
    #: **Never.** manga-ocr's charset holds no Hangul, so handed a Korean crop
    #: it can only emit Japanese -- a confident-looking reading of a language
    #: that is not on the page. Measured on the first real chapter, the
    #: fallback fired on 1 of 152 regions and that is 1 too many: the project
    #: rule is that a wrong-language engine is worse than an error, because
    #: nothing downstream can tell.
    accepts_vertical_fallback = False

    MODEL = "korean_PP-OCRv5_rec.onnx"
    KEYS = "korean_PP-OCRv5_rec_keys.txt"
    requires_files = (MODEL, KEYS)

    def __init__(self) -> None:
        from rapidocr_onnxruntime import RapidOCR

        from comic.paths import models_dir

        model = models_dir() / self.MODEL
        keys = models_dir() / self.KEYS
        missing = [p.name for p in (model, keys) if not p.exists()]
        if missing:
            raise OcrError(
                f"Korean OCR needs {', '.join(missing)} in models/. "
                f"Run: python scripts/fetch_models.py --only korean"
            )
        from comic.cpu import threads_per_worker

        self._engine = RapidOCR(
            rec_model_path=str(model),
            rec_keys_path=str(keys),
            use_det=False,
            use_cls=False,
            intra_op_num_threads=threads_per_worker(),
            inter_op_num_threads=1,
        )

    def read(
        self,
        crop: np.ndarray,
        vertical: bool = False,
        mask: np.ndarray | None = None,
    ) -> OcrOutcome:
        bands = line_bands(crop, mask) or [(0, crop.shape[0])]
        texts: list[str] = []
        scores: list[float] = []
        boxes: list[tuple[float, float, float, float]] = []

        for top, bottom in bands:
            line = crop[top:bottom]
            if not line.size:
                continue
            result, _ = self._engine(line)
            # Recognition-only returns (text, score) pairs, not boxes.
            found = "".join(r[0] for r in (result or [])).strip()
            if not found:
                continue
            texts.append(found)
            scores += [float(r[1]) for r in result if r[1] is not None]
            boxes.append((0.0, float(top), float(crop.shape[1]), float(bottom)))

        if not texts:
            return OcrOutcome("", 0.0, self.name)
        # A wrapped bubble is one sentence, so lines join with a space rather
        # than a newline -- the translate gate wants the sentence, not layout.
        return OcrOutcome(
            " ".join(texts),
            float(np.mean(scores)) if scores else 0.0,
            self.name,
            tuple(boxes),
        )


class MangaOcrProvider:
    """Japanese manga OCR. Handles vertical text and furigana in one pass.

    It returns no confidence score, so a heuristic stands in: the model is
    reliable on real text but emits very short or repetitive output when given
    something that is not text at all.
    """

    name = "manga_ocr"
    languages = frozenset({"ja", "jp", "japanese"})
    supports_vertical = True
    #: Moot: it reads columns itself, so the fallback never fires for it.
    accepts_vertical_fallback = False
    #: Pulls its weights from HuggingFace on first use, not from ``models/``.
    requires_files = ()

    def __init__(self) -> None:
        from manga_ocr import MangaOcr

        self._engine = MangaOcr()

    def read(
        self,
        crop: np.ndarray,
        vertical: bool = False,
        mask: np.ndarray | None = None,
    ) -> OcrOutcome:
        from PIL import Image

        text = (self._engine(Image.fromarray(crop)) or "").strip()
        if not text:
            return OcrOutcome("", 0.0, self.name)
        # A single repeated character is the model's failure mode.
        confidence = 0.55 if len(set(text)) <= 1 else 0.9
        return OcrOutcome(text, confidence, self.name)


#: Order matters only as a tiebreak; language match is what selects.
_PROVIDER_CLASSES = (MangaOcrProvider, KoreanOcrProvider, RapidOcrProvider)


@lru_cache(maxsize=4)
def _load(name: str) -> OcrProvider:
    for cls in _PROVIDER_CLASSES:
        if cls.name == name:
            log.info("loading OCR provider %s", name)
            return cls()  # type: ignore[return-value]
    raise OcrError(f"unknown OCR provider: {name}")


def provider_for(language: str) -> OcrProvider:
    """The provider that serves ``language``.

    Raises rather than silently substituting a wrong-language engine: running
    Chinese OCR over Japanese produces confident nonsense, which is worse than
    an error because nothing downstream can tell.
    """
    key = (language or "").lower()
    for cls in _PROVIDER_CLASSES:
        if key in cls.languages:
            try:
                return _load(cls.name)
            except ImportError as exc:
                raise OcrError(
                    f"{cls.name} serves {language!r} but is not installed: {exc}"
                ) from exc
    known = sorted({lang for cls in _PROVIDER_CLASSES for lang in cls.languages})
    raise OcrError(f"no OCR provider for {language!r}; known languages: {known}")


def available() -> list[tuple[str, bool, str]]:
    """(name, importable, languages) for each provider, for ``comic doctor``."""
    import importlib.util

    modules = {
        "rapidocr": "rapidocr_onnxruntime",
        "rapidocr_ko": "rapidocr_onnxruntime",
        "manga_ocr": "manga_ocr",
    }
    from comic.paths import models_dir

    rows = []
    for cls in _PROVIDER_CLASSES:
        module = modules.get(cls.name, cls.name)
        ok = importlib.util.find_spec(module) is not None
        if ok and getattr(cls, "requires_files", ()):
            ok = all((models_dir() / f).exists() for f in cls.requires_files)
        rows.append((cls.name, ok, ", ".join(sorted(cls.languages))))
    return rows


#: Confidence given to a fallback reading of vertical text.
#:
#: Deliberately below LOW_CONFIDENCE. manga-ocr is the only engine here that
#: reads vertical CJK at all, but it is trained on Japanese: measured against a
#: hand transcription of vertical Chinese it scored 0.62, and returned one to
#: three characters from crops big enough to hold a sentence. That is worth
#: offering as a starting point and not worth trusting, so the region still
#: goes to the agent -- with a candidate rather than a blank.
VERTICAL_FALLBACK_CONFIDENCE = 0.45


def read_region(
    crop: np.ndarray,
    language: str,
    vertical: bool = False,
    mask: np.ndarray | None = None,
) -> OcrOutcome:
    """Read one cropped region, upscaling first when it is small.

    Vertical text gets a second attempt from a vertical-capable engine when the
    primary one comes back empty, which is what PP-OCR does on stacked CJK.
    """
    import cv2

    engine = provider_for(language)
    scale = 1
    if crop.size and crop.shape[0] < UPSCALE_BELOW_HEIGHT:
        scale = UPSCALE_FACTOR
        size = (crop.shape[1] * scale, crop.shape[0] * scale)
        crop = cv2.resize(crop, size, interpolation=cv2.INTER_CUBIC)
        if mask is not None and mask.size:
            # Nearest, and in step with the crop: a mask that no longer lines
            # up with the pixels it describes is worse than no mask at all.
            mask = cv2.resize(mask, size, interpolation=cv2.INTER_NEAREST)
    result = engine.read(crop, vertical=vertical, mask=mask)
    if scale != 1 and result.boxes:
        # Boxes are reported in the enlarged crop; callers measure against the
        # region at its original size.
        result = replace(
            result,
            boxes=tuple(
                tuple(v / scale for v in box)  # type: ignore[misc]
                for box in result.boxes
            ),
        )
    if result.text.strip() or not vertical or engine.supports_vertical:
        return result
    if not getattr(engine, "accepts_vertical_fallback", False):
        # An empty reading is the honest answer. Substituting an engine that
        # cannot spell this language would put unreadable text into the gate
        # request and send the agent looking for the wrong problem.
        return result

    try:
        fallback = _load(MangaOcrProvider.name)
    except (OcrError, ImportError):
        return result
    guess = fallback.read(crop, vertical=True, mask=mask)
    if not guess.text.strip():
        return result
    return OcrOutcome(
        guess.text,
        VERTICAL_FALLBACK_CONFIDENCE,
        f"{fallback.name}:vertical-fallback",
    )
