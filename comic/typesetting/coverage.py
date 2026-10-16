"""Vietnamese glyph coverage checking.

Most display/comic faces cover basic Latin and quietly fall back to tofu (or to a
substituted face) on stacked Vietnamese diacritics -- ``ế ộ ữ ỡ ẳ`` and friends.
The failure is silent at render time and only visible by eye, so fonts are gated
on this check at registration rather than trusted.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from fontTools.ttLib import TTFont, TTLibError

#: The 12 Vietnamese vowel letters in all five tone marks, plus the level tone.
VIETNAMESE_VOWELS = (
    "aàáảãạ"          # a à á ả ã ạ
    "ăằắẳẵặ"      # ă ằ ắ ẳ ẵ ặ
    "âầấẩẫậ"      # â ầ ấ ẩ ẫ ậ
    "eèéẻẽẹ"          # e è é ẻ ẽ ẹ
    "êềếểễệ"      # ê ề ế ể ễ ệ
    "iìíỉĩị"          # i ì í ỉ ĩ ị
    "oòóỏõọ"          # o ò ó ỏ õ ọ
    "ôồốổỗộ"      # ô ồ ố ổ ỗ ộ
    "ơờớởỡợ"      # ơ ờ ớ ở ỡ ợ
    "uùúủũụ"          # u ù ú ủ ũ ụ
    "ưừứửữự"      # ư ừ ứ ử ữ ự
    "yỳýỷỹỵ"          # y ỳ ý ỷ ỹ ỵ
)

#: ``đ`` is the one consonant carrying a diacritic.
VIETNAMESE_CONSONANTS = "đbcdghklmnpqrstvx"

#: Punctuation a comic page actually needs.
REQUIRED_PUNCTUATION = "!?.,;:…—–-“”‘’\"'()[]*/&%#@+<>«»"

DIGITS = "0123456789"

#: Punctuation a face may lack, and what the renderer sets in its place.
#: Letters never get a substitute -- a missing diacritic changes the word --
#: but a comic face without an em dash is common (000 AnimeAce3 lacks all
#: four dashes and guillemets), and a hyphen or straight quote reads the same
#: in a balloon. ``typesetting/render.py: printable`` applies this map. A face
#: missing *letters* is still usable for any line that does not need them:
#: ``render.layout_region`` falls back per line (``missing_for``).
SUBSTITUTES = {
    "—": "-", "–": "-", "«": '"', "»": '"',
    "…": "...", "‘": "'", "’": "'", "“": '"', "”": '"', "[": "(", "]": ")",
    # Hearts: decorative, and no comic face in the set draws one -- left
    # out rather than set as a blank gap (Super Lovers ch.1).
    "♡": "", "♥": "",
}


@lru_cache(maxsize=1)
def required_codepoints() -> frozenset[str]:
    """Every character a Vietnamese comic page may need to render."""
    letters = VIETNAMESE_VOWELS + VIETNAMESE_CONSONANTS
    chars = set(letters) | {c.upper() for c in letters}
    chars |= set(DIGITS) | set(REQUIRED_PUNCTUATION) | {" "}
    return frozenset(chars)


@dataclass(frozen=True)
class CoverageResult:
    path: Path
    family: str
    covered: int
    required: int
    missing: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.missing

    @property
    def ratio(self) -> float:
        return self.covered / self.required if self.required else 0.0

    def describe(self) -> str:
        if self.ok:
            return f"{self.family}: full Vietnamese coverage ({self.covered}/{self.required})"
        sample = " ".join(self.missing[:12])
        more = f" (+{len(self.missing) - 12} more)" if len(self.missing) > 12 else ""
        return f"{self.family}: missing {len(self.missing)} glyphs -> {sample}{more}"


def font_charset(path: Path) -> set[int]:
    """Union of every Unicode cmap subtable in the font."""
    with TTFont(str(path), fontNumber=0, lazy=True) as font:
        chars: set[int] = set()
        for table in font["cmap"].tables:
            if table.isUnicode():
                chars.update(table.cmap.keys())
        return chars


def font_family(path: Path) -> str:
    try:
        with TTFont(str(path), fontNumber=0, lazy=True) as font:
            name = font["name"].getBestFullName() or font["name"].getBestFamilyName()
            return name or path.stem
    except (TTLibError, KeyError, OSError):
        return path.stem


def check_font(path: Path) -> CoverageResult:
    """Report which required characters ``path`` cannot render."""
    required = required_codepoints()
    try:
        available = font_charset(path)
    except (TTLibError, KeyError, OSError) as exc:
        raise ValueError(f"cannot read font {path.name}: {exc}") from exc

    missing = tuple(sorted(
        c for c in required
        if ord(c) not in available
        and not (c in SUBSTITUTES and all(ord(x) in available for x in SUBSTITUTES[c]))
    ))
    if not missing:
        # Present is not the same as drawn: see ``tone_defects``.
        try:
            missing = tone_defects(path)
            if not missing and broken_metrics(path):
                missing = ("<ascent 0>",)
        except OSError:
            pass
    return CoverageResult(
        path=path,
        family=font_family(path),
        covered=len(required) - len(missing),
        required=len(required),
        missing=missing,
    )


def tone_defects(path: Path, size: int = 48) -> tuple[str, ...]:
    """Letters whose glyph cannot be told apart from a sibling's.

    A cmap entry says a character exists, not that it is drawn right. Fan
    conversions routinely map a toned capital to the plain hatted glyph:
    measured on the TeddyBear set, five faces passed the cmap check and set
    "ĐẾN" as "ĐÊN" and "RỒI" as "RÔI" -- a different word, with no tofu to
    give it away. So render each vowel's six tones and flag any that come
    out pixel-identical to another of the six.
    """
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont

    font = ImageFont.truetype(str(path), size)

    def ink(ch: str) -> bytes:
        image = Image.new("L", (size * 2, size * 3), 0)
        ImageDraw.Draw(image).text((size // 2, size), ch, font=font, fill=255)
        return np.asarray(image).tobytes()

    bad: list[str] = []
    for start in range(0, len(VIETNAMESE_VOWELS), 6):
        group = VIETNAMESE_VOWELS[start : start + 6]
        for letters in (group, group.upper()):
            seen: dict[bytes, str] = {}
            for ch in letters:
                key = ink(ch)
                if key in seen or not any(key):
                    # A twin of a sibling tone, or no ink at all.
                    bad.append(ch)
                    continue
                seen[key] = ch
    # Spacing defects (one face set "ĐẾN" as "ĐẾ N") are not caught here: an
    # advance check flagged legitimate wide tildes over a narrow I. Look at a
    # rendered sample before adopting a face.
    for ch in "đĐưƯơƠ":
        if not any(ink(ch)):
            bad.append(ch)
    return tuple(bad)


def broken_metrics(path: Path) -> bool:
    """A face declaring no ascent: its lines cannot be placed by its metrics.

    Measured: both DigitalStrip2 faces report ascent 0, which PIL survives
    (it places by ink) but Photoshop, which leads by the font's metrics, does
    not.
    """
    from PIL import ImageFont

    ascent, _ = ImageFont.truetype(str(path), 48).getmetrics()
    return ascent <= 0


def check_directory(directory: Path) -> list[CoverageResult]:
    """Check every font file in a directory, sorted by family name."""
    if not directory.exists():
        return []
    results = []
    for path in sorted(directory.iterdir()):
        if path.suffix.lower() in {".ttf", ".otf", ".ttc"}:
            try:
                results.append(check_font(path))
            except ValueError:
                continue
    return sorted(results, key=lambda r: r.family.lower())
