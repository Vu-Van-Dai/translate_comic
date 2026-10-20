"""Core on-disk artifact schemas.

Every file the pipeline writes into ``workspace/`` is one of these models. Stages
communicate only through these documents, never through in-memory handoff, which is
what makes any stage independently re-runnable for a single page.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator


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


#: SFX is left untouched by default (spec 27) so the original artwork survives.
UNTRANSLATED_KINDS: frozenset[TextKind] = frozenset({TextKind.SFX})


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

    @property
    def translatable(self) -> bool:
        return self.kind not in UNTRANSLATED_KINDS

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
    #: Pages a visual_qa round changed that still wait for a re-render, because
    #: the round sent some of its lines back to the translate gate first. The
    #: next ``comic render`` without ``--page`` renders only these (and the
    #: rest are reported as unchanged); any render clears the pages it did.
    rerender_pages: list[int] = Field(default_factory=list)

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

    pipeline_version: str = "0.1.0"
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    warnings: list[str] = Field(default_factory=list)

    def stage(self, stage: Stage) -> StageRecord:
        return self.stages.setdefault(stage, StageRecord())

    def page(self, number: int) -> PageRecord | None:
        return next((p for p in self.pages if p.page == number), None)
