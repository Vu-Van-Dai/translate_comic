"""Gate response validation.

The gate protocol's safety property: a malformed agent response reopens the gate
with an explanation rather than corrupting chapter state. That only holds if these
validators are strict, so they are tested directly.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from comic.schemas import Gate, TextKind
from comic.handoff.schemas import (
    RESPONSE_MODELS,
    FixAction,
    OcrReviewResponse,
    QaFix,
    TranslateResponse,
    VisualQaResponse,
)


def fix(action: FixAction, value: str | None = None) -> QaFix:
    return QaFix(region_id="p001_r01", action=action, value=value)


def test_every_gate_has_a_response_model():
    assert set(RESPONSE_MODELS) == set(Gate)


@pytest.mark.parametrize(
    "action",
    [FixAction.FONT_DELTA, FixAction.EXPAND_MASK, FixAction.SHRINK_MASK,
     FixAction.RETRANSLATE_SHORTER],
)
def test_integer_actions_require_an_integer(action):
    with pytest.raises(ValidationError, match="integer"):
        fix(action)
    with pytest.raises(ValidationError, match="integer"):
        fix(action, "smaller")
    assert fix(action, "-2").value == "-2"


def test_nudge_requires_a_coordinate_pair():
    assert fix(FixAction.NUDGE, "0,-6").value == "0,-6"
    for bad in ("3", "", "a,b", "1,2,3"):
        with pytest.raises(ValidationError):
            fix(FixAction.NUDGE, bad)


def test_restyle_must_name_a_real_text_kind():
    assert fix(FixAction.RESTYLE, TextKind.THOUGHT.value)
    with pytest.raises(ValidationError, match="restyle"):
        fix(FixAction.RESTYLE, "whisper")


def test_force_wrap_needs_text():
    assert fix(FixAction.FORCE_WRAP, "Cậu...\nđến thật à...")
    with pytest.raises(ValidationError, match="force_wrap"):
        fix(FixAction.FORCE_WRAP, "   ")


def test_accept_needs_no_value():
    assert fix(FixAction.ACCEPT).value is None


def test_unknown_action_is_rejected():
    """The closed vocabulary is the point: no free-form pixel instructions."""
    with pytest.raises(ValidationError):
        QaFix(region_id="p001_r01", action="redraw_the_page")


def test_passing_qa_cannot_also_request_fixes():
    with pytest.raises(ValidationError, match="passed=true"):
        VisualQaResponse(passed=True, fixes=[fix(FixAction.FONT_DELTA, "-2")])


def test_passing_qa_may_carry_explicit_accepts():
    response = VisualQaResponse(passed=True, fixes=[fix(FixAction.ACCEPT)])
    assert response.passed


def test_responses_reject_unknown_fields():
    """extra='forbid' keeps a hallucinated key from silently doing nothing."""
    with pytest.raises(ValidationError):
        OcrReviewResponse(items=[], confidence=0.9)


def test_translate_response_round_trips():
    payload = TranslateResponse.model_validate(
        {
            "items": [
                {
                    "region_id": "p007_r03",
                    "target_text": "Cậu... đến thật à...",
                    "speaker": "Akira",
                    "addressee": "Yuna",
                }
            ],
            "new_glossary": [{"source": "生徒会", "target": "hội học sinh"}],
            "chapter_summary": "Họ gặp lại nhau sau kỳ nghỉ.",
        }
    )
    assert payload.gate is Gate.TRANSLATE
    assert payload.items[0].target_text.startswith("Cậu")
    restored = TranslateResponse.model_validate_json(payload.model_dump_json())
    assert restored == payload


def test_ocr_review_empty_text_means_drop_the_region():
    response = OcrReviewResponse.model_validate(
        {"items": [{"region_id": "p001_r01", "text": ""}]}
    )
    assert response.items[0].text == ""
