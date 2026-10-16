"""Filesystem layout.

Three roots, deliberately separated (spec 10):

``library/``    final, reading-ready pages only -- nothing else ever lands here
``workspace/``  every intermediate artifact, so one failed page is cheap to redo
``profiles/``   per-series durable knowledge that new chapters inherit
"""

from __future__ import annotations

import os
import re
from pathlib import Path

#: Characters Windows forbids in a path component. Spaces and ``&`` are kept so
#: series folders stay readable, matching the layout in the spec.
_ILLEGAL = r'<>:"/\\|?*'
_ILLEGAL_RE = re.compile(f"[{re.escape(_ILLEGAL)}]")
_WS_RE = re.compile(r"\s+")

#: Names Windows refuses outright, regardless of extension.
_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def project_root() -> Path:
    """Repository root. ``COMIC_HOME`` overrides it for tests."""
    env = os.environ.get("COMIC_HOME")
    if env:
        return Path(env).resolve()
    return Path(__file__).resolve().parent.parent


def safe_name(name: str) -> str:
    """Turn an arbitrary title into a legal, readable directory name.

    Illegal characters become a space rather than being deleted, so a title like
    ``Fate/stay night`` stays readable instead of fusing into ``Fatestay night``.
    """
    cleaned = _ILLEGAL_RE.sub(" ", name)
    cleaned = _WS_RE.sub(" ", cleaned).strip(" .")
    if not cleaned:
        raise ValueError(f"series name {name!r} has no usable characters")
    if cleaned.upper() in _RESERVED:
        cleaned = f"{cleaned}_"
    return cleaned[:120]


def chapter_label(chapter: str | float | int) -> str:
    """Normalise a chapter identifier into its folder name.

    Numeric chapters are zero-padded to three digits so file explorers sort them
    correctly; decimal side-chapters keep their fraction. Non-numeric labels such
    as ``Extra`` or ``Special`` pass through as-is.

    >>> chapter_label(1), chapter_label("10"), chapter_label("1.5"), chapter_label("Extra")
    ('Chapter_001', 'Chapter_010', 'Chapter_001.5', 'Extra')
    """
    raw = str(chapter).strip()
    if not raw:
        raise ValueError("chapter identifier is empty")
    if raw.lower().startswith("chapter"):
        raw = raw[len("chapter"):].lstrip(" _-")

    m = re.fullmatch(r"(\d+)(?:\.(\d+))?", raw)
    if m:
        whole, frac = m.group(1), m.group(2)
        return f"Chapter_{int(whole):03d}.{frac}" if frac else f"Chapter_{int(whole):03d}"
    return safe_name(raw)


def chapter_sort_key(label: str) -> tuple[int, float, str]:
    """Order chapter folders numerically, with named extras last."""
    m = re.fullmatch(r"Chapter_(\d+)(?:\.(\d+))?", label)
    if m:
        return (0, float(f"{m.group(1)}.{m.group(2) or 0}"), "")
    return (1, 0.0, label.lower())


def page_filename(page: int, ext: str = ".png") -> str:
    """Zero-padded page name (spec 9). Source filenames are always discarded."""
    if page < 1:
        raise ValueError(f"page numbers are 1-based, got {page}")
    if not ext.startswith("."):
        ext = f".{ext}"
    return f"{page:03d}{ext.lower()}"


class ChapterPaths:
    """Every path a stage needs for one chapter, derived from series + chapter."""

    def __init__(self, series: str, chapter: str, root: Path | None = None) -> None:
        self.root = root or project_root()
        self.series = safe_name(series)
        self.chapter = chapter_label(chapter)

    # -- workspace ---------------------------------------------------------
    @property
    def workspace(self) -> Path:
        return self.root / "workspace" / self.series / self.chapter

    @property
    def original(self) -> Path:
        """Untouched source pages, renamed but not otherwise modified."""
        return self.workspace / "original"

    @property
    def pages(self) -> Path:
        """Re-cut pages. Only a long strip ever writes here."""
        return self.workspace / "pages"

    @property
    def source_pages(self) -> Path:
        """The directory every stage after ``slice`` reads its pages from.

        ``original/`` is the verbatim archive of what the source served and is
        never edited, so a webtoon's re-cut pages have to live somewhere else.
        Every other stage wants "the pages", whichever of the two that is, and
        picking by what exists keeps the proven paged path byte-identical --
        nothing is copied for a source that was already paged.
        """
        if self.pages.is_dir() and any(self.pages.glob("*.png")):
            return self.pages
        return self.original

    def page_file(self, page: int) -> Path:
        """The actual file holding one page, whatever it is stored as.

        ``original/`` keeps the bytes the source served, so a JPEG host leaves
        ``001.jpg`` there while a re-cut strip writes ``001.png``. Building the
        name as ``f"{page:03d}.png"`` therefore works on one kind of chapter
        and raises FileNotFoundError on the other -- which is exactly how it
        failed: detection and OCR both finished, and the run died writing the
        review crops, after all the expensive work.

        Resolve by stem instead. Falls back to the ``.png`` spelling when
        nothing matches, so a genuinely missing page still reports the name a
        reader expects.
        """
        for candidate in sorted(self.source_pages.glob(f"{page:03d}.*")):
            if candidate.is_file():
                return candidate
        return self.source_pages / page_filename(page)

    @property
    def analysis(self) -> Path:
        return self.workspace / "analysis"

    @property
    def masks(self) -> Path:
        return self.workspace / "masks"

    @property
    def cleaned(self) -> Path:
        return self.workspace / "cleaned"

    @property
    def translated(self) -> Path:
        """Rendered pages, before they are accepted into the library."""
        return self.workspace / "translated"

    @property
    def handoff(self) -> Path:
        return self.workspace / "handoff"

    @property
    def temp(self) -> Path:
        return self.workspace / "temp"

    @property
    def logs(self) -> Path:
        return self.workspace / "logs"

    @property
    def state_file(self) -> Path:
        return self.workspace / "chapter.json"

    @property
    def translation_file(self) -> Path:
        return self.workspace / "translation.json"

    # -- library -----------------------------------------------------------
    @property
    def library(self) -> Path:
        return self.root / "library" / self.series / self.chapter

    # -- profile -----------------------------------------------------------
    @property
    def profile_dir(self) -> Path:
        return self.root / "profiles" / self.series

    def analysis_file(self, page: int) -> Path:
        return self.analysis / f"page_{page:03d}.json"

    def mask_file(self, page: int) -> Path:
        return self.masks / f"page_{page:03d}.png"

    def gate_request(self, gate: str) -> Path:
        return self.handoff / f"{gate}_request.json"

    def gate_response(self, gate: str) -> Path:
        return self.handoff / f"{gate}_response.json"

    def gate_dir(self, gate: str) -> Path:
        return self.handoff / gate

    def ensure(self) -> None:
        """Create the workspace tree. Library is created only at publish time."""
        for p in (
            self.original, self.pages, self.analysis, self.masks, self.cleaned,
            self.translated, self.handoff, self.temp, self.logs,
        ):
            p.mkdir(parents=True, exist_ok=True)

    def __repr__(self) -> str:
        return f"ChapterPaths({self.series!r}, {self.chapter!r})"


class SeriesPaths:
    """Durable per-series knowledge, inherited by every new chapter (spec 6)."""

    def __init__(self, series: str, root: Path | None = None) -> None:
        self.root = root or project_root()
        self.series = safe_name(series)

    @property
    def profile_dir(self) -> Path:
        return self.root / "profiles" / self.series

    @property
    def profile_file(self) -> Path:
        return self.profile_dir / "series.yaml"

    @property
    def characters_file(self) -> Path:
        return self.profile_dir / "characters.json"

    @property
    def glossary_file(self) -> Path:
        return self.profile_dir / "glossary.json"

    @property
    def fonts_file(self) -> Path:
        return self.profile_dir / "fonts.yaml"

    @property
    def memory_dir(self) -> Path:
        return self.profile_dir / "memory"

    @property
    def summaries_file(self) -> Path:
        return self.memory_dir / "chapter_summaries.json"

    @property
    def library_dir(self) -> Path:
        return self.root / "library" / self.series

    @property
    def workspace_dir(self) -> Path:
        return self.root / "workspace" / self.series

    def exists(self) -> bool:
        return self.profile_file.exists()

    def chapters(self) -> list[str]:
        """Chapter folders already present in the workspace, in reading order."""
        if not self.workspace_dir.exists():
            return []
        labels = [p.name for p in self.workspace_dir.iterdir() if p.is_dir()]
        return sorted(labels, key=chapter_sort_key)

    def ensure(self) -> None:
        self.memory_dir.mkdir(parents=True, exist_ok=True)

    def __repr__(self) -> str:
        return f"SeriesPaths({self.series!r})"


def browser_profile_dir(root: Path | None = None) -> Path:
    """Persistent Playwright profile, so a site login survives between runs."""
    return (root or project_root()) / "profiles" / "_browser"


def fonts_dir(root: Path | None = None) -> Path:
    return (root or project_root()) / "assets" / "fonts"


def models_dir(root: Path | None = None) -> Path:
    return (root or project_root()) / "models"
