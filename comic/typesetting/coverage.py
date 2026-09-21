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

    missing = tuple(sorted(c for c in required if ord(c) not in available))
    return CoverageResult(
        path=path,
        family=font_family(path),
        covered=len(required) - len(missing),
        required=len(required),
        missing=missing,
    )


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
