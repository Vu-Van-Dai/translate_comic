"""Gate request/response contracts.

A *gate* is a point where the pipeline refuses to guess. It writes a request
document plus the exact image crops the agent needs to look at, then halts. The
agent reads those images, writes a response document, and the run resumes.

Responses are validated against these models before anything is applied, so a
malformed answer reopens the gate with an explanation instead of corrupting state.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import Field, model_validator

from comic.schemas import Base, Gate, TextKind, utcnow


class GateRequest(Base):
    """Common envelope. ``instructions`` is also printed to the terminal."""

    gate: Gate
    series: str
    chapter: str
    created_at: datetime = Field(default_factory=utcnow)
    #: Written by the pipeline when a previous response failed validation.
    previous_error: str | None = None
    instructions: str = ""


# ------------------------------------------------------------------- ocr_review


class OcrReviewItem(Base):
    region_id: str
    page: int
    #: Crop written at 3x upscale for legibility.
    crop_path: str
    kind: TextKind
    is_vertical: bool
    source_language: str | None = None
    engine_text: str = ""
    engine_confidence: float | None = None
    reason: str = ""


class OcrReviewRequest(GateRequest):
    gate: Gate = Gate.OCR_REVIEW
    items: list[OcrReviewItem] = Field(default_factory=list)
    #: page -> one labelled sheet holding every crop on that page. Read this
    #: instead of the individual crops; they remain for anything ambiguous.
    sheets: dict[int, str] = Field(default_factory=dict)


class OcrReviewItemResponse(Base):
    region_id: str
    #: Transcribe exactly what is printed, in the source language. Empty string
    #: means the region holds no readable text and should be dropped.
    text: str
    #: Override the detector's guess when the crop clearly shows otherwise.
    kind: TextKind | None = None
    is_vertical: bool | None = None


class OcrReviewResponse(Base):
    gate: Gate = Gate.OCR_REVIEW
    items: list[OcrReviewItemResponse] = Field(default_factory=list)


# --------------------------------------------------------------------- translate


class TranslateItem(Base):
    region_id: str
    page: int
    panel_index: int | None
    reading_order: int
    kind: TextKind
    source_text: str
    crop_path: str
    #: Usable width/height of the bubble interior, so the agent can judge how long
    #: a line may be before it will not fit.
    fit_width: int
    fit_height: int
    #: Rough budget from bubble area and the font profile; advisory, not enforced.
    suggested_max_chars: int | None = None
    #: Set when `visual_qa` returned `retranslate_shorter` for this region: the
    #: rendered line did not fit and the translation has to be shorter, not the
    #: type smaller. Unlike the budget above, this is why the gate reopened.
    must_fit_chars: int | None = None
    shorten_reason: str | None = None
    speaker_hint: str | None = None


class TranslatePageContext(Base):
    page: int
    #: Full page, downscaled, with numbered region overlays matching ``region_id``.
    overlay_path: str
    items: list[TranslateItem] = Field(default_factory=list)


class TranslateRequest(GateRequest):
    gate: Gate = Gate.TRANSLATE
    source_language: str
    target_language: str = "vi"
    style_mode: str = "published_comic"
    #: Injected series context: profile, characters, pronoun matrix, glossary and
    #: the previous chapter's summary, rendered as markdown for direct reading.
    context_path: str = ""
    pages: list[TranslatePageContext] = Field(default_factory=list)


class TranslateItemResponse(Base):
    region_id: str
    target_text: str
    speaker: str | None = None
    addressee: str | None = None
    note: str | None = None


class GlossaryProposal(Base):
    source: str
    target: str
    note: str | None = None


class CharacterObservation(Base):
    name: str
    #: Free-form deltas merged into character memory after the chapter completes.
    speech_style: str | None = None
    personality: str | None = None
    note: str | None = None


class AddressObservation(Base):
    """How one character addressed another, as used in this chapter.

    Vietnamese has no neutral second person, so every line commits to a
    relationship, and a pair that drifts between chapters reads as a different
    relationship. Only the agent can say which pair it used -- the choice lives
    in the wording, not in anything the pipeline can measure -- so it is
    reported here and pinned for every chapter that follows.
    """

    speaker: str
    addressee: str
    #: What the speaker calls themselves, and what they call the listener. The
    #: two move together in Vietnamese; splitting them produces sentences no
    #: one would say.
    self_term: str
    other_term: str
    formality: str = "neutral"
    note: str | None = None


class TranslateResponse(Base):
    gate: Gate = Gate.TRANSLATE
    items: list[TranslateItemResponse] = Field(default_factory=list)
    #: Terms worth pinning for future chapters.
    new_glossary: list[GlossaryProposal] = Field(default_factory=list)
    character_notes: list[CharacterObservation] = Field(default_factory=list)
    #: The pronoun pairs used, one per ordered (speaker, addressee).
    address_pairs: list[AddressObservation] = Field(default_factory=list)
    chapter_summary: str | None = None


# --------------------------------------------------------------------- visual_qa


class FixAction(str, Enum):
    """Closed vocabulary. The agent decides *what is wrong*; the pipeline decides
    how to change pixels. Anything outside this set is rejected by validation."""

    ACCEPT = "accept"
    FONT_DELTA = "font_delta"              # value: int point delta, e.g. -2
    NUDGE = "nudge"                        # value: "dx,dy" in pixels
    FORCE_WRAP = "force_wrap"              # value: text with explicit newlines
    EXPAND_MASK = "expand_mask"            # value: int pixels to dilate
    SHRINK_MASK = "shrink_mask"            # value: int pixels to erode
    FORCE_INPAINT = "force_inpaint"        # value: unused; escalate to tier 2
    RETRANSLATE_SHORTER = "retranslate_shorter"  # value: int max chars
    RESTYLE = "restyle"                    # value: a TextKind name


#: Actions whose ``value`` must parse as an int.
_INT_ACTIONS = {
    FixAction.FONT_DELTA,
    FixAction.EXPAND_MASK,
    FixAction.SHRINK_MASK,
    FixAction.RETRANSLATE_SHORTER,
}


class QaPreCheck(Base):
    """Machine-computed findings handed to the agent alongside the images."""

    region_id: str
    code: str        # e.g. "overflow", "tiny_font", "mask_residue"
    detail: str
    severity: str = "warning"   # info | warning | error


class VisualQaPageItem(Base):
    page: int
    rendered_path: str
    original_path: str
    pre_checks: list[QaPreCheck] = Field(default_factory=list)


class VisualQaRequest(GateRequest):
    gate: Gate = Gate.VISUAL_QA
    round: int = 1
    max_rounds: int = 3
    pages: list[VisualQaPageItem] = Field(default_factory=list)


class QaFix(Base):
    region_id: str
    action: FixAction
    value: str | None = None
    reason: str | None = None

    @model_validator(mode="after")
    def _check_value(self) -> QaFix:
        if self.action in _INT_ACTIONS:
            if self.value is None:
                raise ValueError(f"action {self.action.value} requires an integer value")
            try:
                int(self.value)
            except ValueError as exc:
                raise ValueError(
                    f"action {self.action.value} needs an integer value, got {self.value!r}"
                ) from exc
        elif self.action is FixAction.NUDGE:
            parts = (self.value or "").split(",")
            if len(parts) != 2:
                raise ValueError("nudge needs a 'dx,dy' value, e.g. '0,-6'")
            for p in parts:
                try:
                    int(p.strip())
                except ValueError as exc:
                    raise ValueError(f"nudge components must be integers, got {self.value!r}") from exc
        elif self.action is FixAction.RESTYLE:
            valid = {k.value for k in TextKind}
            if self.value not in valid:
                raise ValueError(f"restyle value must be one of {sorted(valid)}")
        elif self.action is FixAction.FORCE_WRAP:
            if not (self.value or "").strip():
                raise ValueError("force_wrap needs the wrapped text as its value")
        return self


class VisualQaResponse(Base):
    gate: Gate = Gate.VISUAL_QA
    #: True when every page reads correctly and the chapter may be published.
    passed: bool = False
    fixes: list[QaFix] = Field(default_factory=list)
    #: Pages the agent could not resolve; these escalate to the user.
    blocked: list[str] = Field(default_factory=list)
    note: str | None = None

    @model_validator(mode="after")
    def _check_consistency(self) -> VisualQaResponse:
        actionable = [f for f in self.fixes if f.action is not FixAction.ACCEPT]
        if self.passed and actionable:
            raise ValueError(
                "passed=true cannot be combined with actionable fixes; "
                "either set passed=false or drop the fixes"
            )
        return self


#: Lets the resume command pick the right model without a chain of ifs.
REQUEST_MODELS: dict[Gate, type[GateRequest]] = {
    Gate.OCR_REVIEW: OcrReviewRequest,
    Gate.TRANSLATE: TranslateRequest,
    Gate.VISUAL_QA: VisualQaRequest,
}

RESPONSE_MODELS: dict[Gate, type[Base]] = {
    Gate.OCR_REVIEW: OcrReviewResponse,
    Gate.TRANSLATE: TranslateResponse,
    Gate.VISUAL_QA: VisualQaResponse,
}
