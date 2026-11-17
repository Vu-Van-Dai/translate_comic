"""Core on-disk artifact schemas.

Every file the pipeline writes into ``workspace/`` is one of these models. Stages
communicate only through these documents, never through in-memory handoff, which is
what makes any stage independently re-runnable for a single page.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------- enums


class ReadingDirection(str, Enum):
    RTL = "rtl"   # Japanese manga: right-to-left, top-to-bottom
    LTR = "ltr"   # Western comics
    TTB = "ttb"   # Webtoon / long strip: pure vertical


class PageKind(str, Enum):
    PAGED = "paged"
    LONGSTRIP = "longstrip"


class TextKind(str, Enum):
    DIALOGUE = "dialogue"
    NARRATION = "narration"
    THOUGHT = "thought"
    SHOUT = "shout"
    CAPTION = "caption"
    SFX = "sfx"
    #: Another group's logo or a site watermark (the operator, 2026-10-28):
    #: erased like lettering, never translated or typeset.
    ERASE = "erase"
    #: Lettering left exactly as drawn: in-world signage, a book cover, a shop
    #: sign (the operator's Studio review, 2026-11-08). Neither cleaned nor
    #: translated; kept as a region so a re-derivation does not resurrect it.
    KEEP = "keep"


#: SFX is left untouched by default (spec 27) so the original artwork survives.
UNTRANSLATED_KINDS: frozenset[TextKind] = frozenset({TextKind.SFX})

#: ``TextRegion.tag`` of an SFX that LaMa erases at cleaning (Studio key L).
LAMA_SFX_TAG = "lama"


class Stage(str, Enum):
    DOWNLOAD = "download"
    SLICE = "slice"
    DETECT = "detect"
    OCR = "ocr"
    TRANSLATE = "translate"
    CLEAN = "clean"
    TYPESET = "typeset"
    QA = "qa"
    PUBLISH = "publish"


#: Canonical execution order. ``rerun --from <stage>`` replays this suffix.
STAGE_ORDER: tuple[Stage, ...] = (
    Stage.DOWNLOAD,
    Stage.SLICE,
    Stage.DETECT,
    Stage.OCR,
    Stage.TRANSLATE,
    Stage.CLEAN,
    Stage.TYPESET,
    Stage.QA,
    Stage.PUBLISH,
)


class StageStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


class ChapterStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    AWAITING_AGENT = "awaiting_agent"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


class Gate(str, Enum):
    """Points where the pipeline stops and asks the agent to look at images."""

    OCR_REVIEW = "ocr_review"
    TRANSLATE = "translate"
    VISUAL_QA = "visual_qa"


# ------------------------------------------------------------------------ geometry

Point = tuple[int, int]


class BBox(Base):
    x1: int
    y1: int
    x2: int
    y2: int

    @field_validator("x2")
    @classmethod
    def _check_width(cls, v: int, info):
        if v <= info.data.get("x1", 0):
            raise ValueError("x2 must be greater than x1")
        return v

    @field_validator("y2")
    @classmethod
    def _check_height(cls, v: int, info):
        if v <= info.data.get("y1", 0):
            raise ValueError("y2 must be greater than y1")
        return v

    @property
    def width(self) -> int:
        return self.x2 - self.x1

    @property
    def height(self) -> int:
        return self.y2 - self.y1

    @property
    def area(self) -> int:
        return self.width * self.height

    @property
    def center(self) -> Point:
        return ((self.x1 + self.x2) // 2, (self.y1 + self.y2) // 2)

    def padded(self, pad: int, bounds: tuple[int, int] | None = None) -> BBox:
        x1, y1 = self.x1 - pad, self.y1 - pad
        x2, y2 = self.x2 + pad, self.y2 + pad
        if bounds is not None:
            w, h = bounds
            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(w, x2), min(h, y2)
        return BBox(x1=x1, y1=y1, x2=x2, y2=y2)


# ------------------------------------------------------------------------ analysis


def region_id(page: int, index: int) -> str:
    """Stable, sortable region identifier, e.g. ``p007_r03``."""
    return f"p{page:03d}_r{index:02d}"


class Panel(Base):
    index: int
    bbox: BBox


def rotated_corners(box: tuple[float, float, float, float], angle: float) -> list[tuple[float, float]]:
    """Corners (nw, ne, se, sw) of the upright box ``box`` turned ``angle``
    degrees clockwise about its centre, in image coordinates (y down)."""
    import math

    x1, y1, x2, y2 = box
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    t = math.radians(angle)
    c, s_ = math.cos(t), math.sin(t)
    out = []
    for x, y in ((x1, y1), (x2, y1), (x2, y2), (x1, y2)):
        dx, dy = x - cx, y - cy
        out.append((cx + dx * c - dy * s_, cy + dx * s_ + dy * c))
    return out


def unrotated_box(corners: list, angle: float) -> tuple[float, float, float, float]:
    """The upright box that ``rotated_corners`` turned into ``corners``."""
    import math

    cx = sum(p[0] for p in corners) / 4
    cy = sum(p[1] for p in corners) / 4
    w = math.dist(corners[0], corners[1])
    h = math.dist(corners[1], corners[2])
    return (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)


class TextRegion(Base):
    id: str
    page: int
    bbox: BBox
    kind: TextKind

    #: Tight text-mask outline; falls back to the bbox rectangle when the detector
    #: returns no polygon.
    polygon: list[Point] = Field(default_factory=list)
    #: Bubble interior outline, when the region sits inside a detected bubble. The
    #: typesetter fits text to this rather than to the bbox, so round bubbles do
    #: not overflow at the corners.
    bubble_polygon: list[Point] = Field(default_factory=list)

    panel_index: int | None = None
    reading_order: int = 0
    is_vertical: bool = False

    # populated by the OCR stage
    source_text: str = ""
    source_language: str | None = None
    ocr_confidence: float | None = None
    ocr_provider: str | None = None
    #: Share of the detector's text mask that no OCR run covered. High means the
    #: engine never saw some of the text, which it reports at full confidence
    #: because what it did read was read correctly.
    ocr_unread: float | None = None
    #: Set when an agent transcription superseded the OCR engine output.
    ocr_reviewed: bool = False

    #: What an erase-only region is, as the operator labelled it in Studio:
    #: ``logo``, ``watermark`` or ``credit`` -- purely descriptive. On an SFX,
    #: ``lama`` (:data:`LAMA_SFX_TAG`, Studio key L) means LaMa erases it at
    #: cleaning instead of Photoshop at the PSD build; see :attr:`lama_sfx`.
    tag: str | None = None
    #: Drawn or reshaped by the operator in Studio. The detector's glyph mask
    #: may not cover such a box, so cleaning finds its letters itself.
    edited: bool = False
    #: Tilt of the box the operator drew in Studio, in degrees clockwise
    #: (2026-11-09). When set, ``polygon`` holds the tilted rectangle's four
    #: corners and ``bbox`` its upright bounds, so code that only knows boxes
    #: still covers the lettering; cleaning, the check's crops and the PSD's
    #: text layer follow the tilt.
    angle: float = Field(default=0.0, ge=-180, le=180)
    #: Set by the transcription check when the agent leaves this region for the
    #: operator (2026-11-08). The region is kept and cleaned like any other,
    #: never translated by the pipeline, and listed in ``untranslated.txt``.
    hold_reason: str | None = None

    @property
    def translatable(self) -> bool:
        return self.kind not in UNTRANSLATED_KINDS and self.kind not in (
            TextKind.ERASE, TextKind.KEEP
        )

    @property
    def lama_sfx(self) -> bool:
        """An SFX the operator boxed with Studio's key L (2026-11-14).

        Checked, translated and set like any SFX, but erased by LaMa at
        cleaning -- whole box, as a group mark is -- instead of by Content-Aware
        Fill at the PSD build, which botched the hard ones and had to be redone
        by hand. Untranslated, its spot is simply the cleaned art.
        """
        return self.kind is TextKind.SFX and self.tag == LAMA_SFX_TAG

    @property
    def erased_whole(self) -> bool:
        """Erased at cleaning as a whole box, always by LaMa: another group's
        logo / watermark / credit, or an SFX marked for LaMa."""
        return self.kind is TextKind.ERASE or self.lama_sfx

    @property
    def erasable(self) -> bool:
        """Cleaned from the page: every translatable region, erase-only ones,
        and SFX marked for LaMa."""
        return self.translatable or self.erased_whole

    @property
    def cleanable(self) -> bool:
        """Actually erased by cleaning.

        An empty reading on a detector box means "leave it" (front matter
        answered "" at ``ocr_review``), so that box stays. A logo, watermark or
        credit is different: it is a picture or faint grey that OCR seldom
        reads, and the label alone says to erase it. A box the operator drew or
        reshaped in Studio is there because they saw lettering. Both are erased
        whatever OCR made of them -- gating them on text skipped every
        watermark drawn by hand in Studio (the operator, 2026-11-12: jmcomic
        and mw123.cc marks left on Kawaii ch.4 after cleaning).
        """
        if not self.erasable:
            return False
        return bool(self.source_text.strip()) or self.erased_whole or self.edited

    @property
    def reading_looks_truncated(self) -> bool:
        """Whether OCR left part of the detected text unread.

        Kept here rather than in the OCR stage so a re-run can judge a stored
        reading without re-running the engine.
        """
        from comic.ocr.registry import MAX_UNREAD_MASK

        return self.ocr_unread is not None and self.ocr_unread > MAX_UNREAD_MASK


class PageAnalysis(Base):
    page: int
    width: int
    height: int
    reading_direction: ReadingDirection
    panels: list[Panel] = Field(default_factory=list)
    regions: list[TextRegion] = Field(default_factory=list)
    #: True when the page is effectively grayscale; drives screentone-safe cleaning.
    is_grayscale: bool = False
    analyzed_at: datetime = Field(default_factory=utcnow)

    def region(self, rid: str) -> TextRegion | None:
        return next((r for r in self.regions if r.id == rid), None)


# --------------------------------------------------------------------- translation


class TranslationEntry(Base):
    region_id: str
    source_text: str
    target_text: str
    speaker: str | None = None
    addressee: str | None = None
    #: Free-form rationale, surfaced back to the agent on a rerun so it can see why
    #: a line was phrased the way it was last time.
    note: str | None = None
    #: A named voice from the series' ``fonts.yaml`` (``voices:``): the face
    #: this one line is set in -- a stammer, a whisper, a cheerful aside. The
    #: agent picks it from the panel; None sets the line in its kind's face.
    voice: str | None = None


class ChapterTranslation(Base):
    series: str
    chapter: str
    entries: list[TranslationEntry] = Field(default_factory=list)
    translated_at: datetime = Field(default_factory=utcnow)

    def by_region(self) -> dict[str, TranslationEntry]:
        return {e.region_id: e for e in self.entries}


class RegionOverride(Base):
    """Per-region adjustments a `visual_qa` fix produced.

    The agent names what is wrong in a closed vocabulary; this is what that
    vocabulary turns into. Keeping it as a stored document rather than applying
    it in place means a re-render is reproducible, a fix can be undone, and the
    page is always rebuilt from the original rather than edited repeatedly.
    """

    #: Points added to the fitted size. Negative shrinks.
    font_delta: int = 0
    dx: int = 0
    dy: int = 0
    #: Explicit line breaks that override automatic wrapping.
    forced_lines: list[str] = Field(default_factory=list)
    #: Pixels to grow (positive) or shrink (negative) the cleaning mask.
    mask_grow: int = 0
    #: Skip the flat fill and inpaint this region instead.
    force_inpaint: bool = False
    #: Render as a different kind of text than detection decided.
    style_kind: TextKind | None = None
    #: Set in a named voice from ``fonts.yaml``; wins over ``style_kind`` and
    #: over the voice chosen at translation.
    voice: str | None = None
    #: Set by ``retranslate_shorter``; the translate gate reopens for these.
    max_chars: int | None = None


class RenderPlan(Base):
    series: str
    chapter: str
    #: How many QA rounds have been applied. Capped so a loop cannot run away.
    round: int = 1
    overrides: dict[str, RegionOverride] = Field(default_factory=dict)
    #: Page -> digest of every input of the render last shown at visual_qa
    #: (``qa.visual.page_fingerprints``).
    shown: dict[int, str] = Field(default_factory=dict)
    #: Page -> digest of the render the agent approved: shown, not fixed, not
    #: blocked, and no error-level pre-check left unanswered. A page whose
    #: inputs still hash the same renders pixel-identical, so later rounds do
    #: not render or show it again. Cleared when the chapter is published.
    approved: dict[int, str] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _drop_retired(cls, data):
        # ``rerender_pages`` (2026-10-20, replaced the same day by ``approved``)
        # may sit in a plan written in between; it carries nothing still used.
        if isinstance(data, dict):
            data = {k: v for k, v in data.items() if k != "rerender_pages"}
        return data

    def for_region(self, region_id: str) -> RegionOverride:
        return self.overrides.get(region_id) or RegionOverride()

    def with_voices(self, translation: "ChapterTranslation | None") -> "RenderPlan":
        """A copy with each line's translation-time voice folded in.

        For rendering only, never saved: a voice chosen at the translate gate
        lives in ``translation.json``, so retranslating can change it, while a
        ``visual_qa`` restyle (``RegionOverride.voice``) still wins.
        """
        plan = self.model_copy(deep=True)
        for entry in (translation.entries if translation else []):
            if entry.voice:
                current = plan.for_region(entry.region_id)
                # A restyle to a kind at visual_qa is a choice too: it beats
                # the translation's voice rather than being undone by it.
                if current.voice is None and current.style_kind is None:
                    plan.edit(entry.region_id).voice = entry.voice
        return plan

    def edit(self, region_id: str) -> RegionOverride:
        """The override for a region, created on first use."""
        current = self.overrides.get(region_id)
        if current is None:
            current = RegionOverride()
            self.overrides[region_id] = current
        return current


# --------------------------------------------------------------------------- state


class PageRecord(Base):
    page: int
    filename: str
    width: int | None = None
    height: int | None = None
    source_url: str | None = None
    sha256: str | None = None
    #: Per-page stage status, so one bad page never forces a whole-chapter redo.
    stages: dict[Stage, StageStatus] = Field(default_factory=dict)
    #: Not part of the work: drop it instead of publishing it.
    #:
    #: Distinct from a page that merely has nothing to translate. A cover or a
    #: wordless splash *is* the comic, so it is published untranslated and the
    #: page numbers keep matching the source. An uploader's insert -- a
    #: scanlation group's credits card, a "read it on <host>" notice, an ad --
    #: is not the comic at all, and leaving it in the library publishes
    #: somebody else's banner as though it were a page of the story.
    #:
    #: Kept in ``original/`` regardless: that stays the verbatim archive of
    #: what the source served. This only decides what reaches ``library/``.
    excluded: bool = False
    #: Excluded, but still a page of the comic: held back for its content (the
    #: operator, 2026-10-27). Unlike an insert it keeps its number when the
    #: Photoshop route renumbers, so the pages after it still match the
    #: source, and a file the operator puts at that number is never removed.
    held: bool = False
    #: Why the page is held, in a line the operator can act on.
    hold_reason: str | None = None
    #: A held page that may still have logos and site watermarks erased
    #: (``scripts/erase_marks.py``), never anything else (the operator,
    #: 2026-10-28). Off for a page held for sexual content.
    hold_cleanup: bool = False

    def status_of(self, stage: Stage) -> StageStatus:
        return self.stages.get(stage, StageStatus.PENDING)


class StageRecord(Base):
    status: StageStatus = StageStatus.PENDING
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None
    #: Short human-readable outcome, e.g. "37 pages, 214 regions".
    summary: str | None = None


class ChapterState(Base):
    """``workspace/<series>/<chapter>/chapter.json`` -- the resumability record."""

    series: str
    chapter: str
    source_url: str | None = None
    source_site: str | None = None
    source_language: str = "ja"
    target_language: str = "vi"
    page_kind: PageKind = PageKind.PAGED
    reading_direction: ReadingDirection = ReadingDirection.RTL

    status: ChapterStatus = ChapterStatus.PENDING
    pending_gate: Gate | None = None
    #: Set when a gate response failed validation, so the reopened gate can tell the
    #: agent exactly what was wrong with its previous answer.
    gate_error: str | None = None

    page_count: int = 0
    pages: list[PageRecord] = Field(default_factory=list)
    stages: dict[Stage, StageRecord] = Field(default_factory=dict)

    #: Why this chapter as a whole cannot be processed, or None. A block is
    #: the chapter's alone (the operator, 2026-10-28): the series queue skips
    #: it and carries on with the next chapter, and other chapters' Photoshop
    #: and SFX work is unaffected. One held page is not a block -- see
    #: ``PageRecord.held``.
    blocked: str | None = None

    pipeline_version: str = "0.1.0"
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    warnings: list[str] = Field(default_factory=list)

    def stage(self, stage: Stage) -> StageRecord:
        return self.stages.setdefault(stage, StageRecord())

    def page(self, number: int) -> PageRecord | None:
        return next((p for p in self.pages if p.page == number), None)

    @property
    def held_pages(self) -> list[PageRecord]:
        return [p for p in self.pages if p.held]

    @property
    def content_status(self) -> str:
        """``blocked`` (whole chapter), ``partial`` (some pages held) or ``ok``."""
        if self.blocked:
            return "blocked"
        return "partial" if self.held_pages else "ok"
