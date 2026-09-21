"""State machine and resumability (spec 31, 32)."""

from __future__ import annotations

import pytest

from comic.paths import ChapterPaths
from comic.schemas import (
    ChapterStatus,
    Gate,
    PageRecord,
    Stage,
    StageStatus,
)
from comic.state.store import (
    ChapterStore,
    StateError,
    close_gate,
    invalidate_from,
    next_stage,
    open_gate,
    pages_needing,
    running,
    stages_from,
)

DONE_THROUGH_OCR = (Stage.DOWNLOAD, Stage.SLICE, Stage.DETECT, Stage.OCR)


@pytest.fixture
def store(chapter: ChapterPaths) -> ChapterStore:
    return ChapterStore(chapter)


@pytest.fixture
def populated(store: ChapterStore):
    """A chapter of 5 pages, complete through OCR."""
    state = store.create(source_url="https://example.test/ch69", source_language="ko")
    state.pages = [PageRecord(page=i, filename=f"{i:03d}.png") for i in range(1, 6)]
    state.page_count = 5
    for page in state.pages:
        for stage in DONE_THROUGH_OCR:
            page.stages[stage] = StageStatus.DONE
    for stage in DONE_THROUGH_OCR:
        state.stage(stage).status = StageStatus.DONE
    store.save(state)
    return store, store.load()


def test_state_round_trips_through_json(populated):
    store, state = populated
    store.save(state)
    reloaded = store.load()
    assert reloaded.page_count == 5
    assert reloaded.source_language == "ko"
    assert reloaded.stage(Stage.OCR).status is StageStatus.DONE


def test_next_stage_is_first_incomplete(populated):
    _, state = populated
    assert next_stage(state) is Stage.TRANSLATE


def test_stages_from_returns_suffix():
    assert stages_from(Stage.CLEAN) == (
        Stage.CLEAN, Stage.TYPESET, Stage.QA, Stage.PUBLISH,
    )


def test_rerun_single_page_leaves_other_pages_alone(populated):
    """The core resumability guarantee: one bad page is cheap (spec 31)."""
    store, state = populated
    invalidate_from(state, Stage.OCR, pages=[3])

    assert [p.page for p in pages_needing(state, Stage.OCR)] == [3]
    # Upstream work survives for every page, including the reset one.
    assert all(p.status_of(Stage.DETECT) is StageStatus.DONE for p in state.pages)
    # Downstream of OCR is reset only for page 3.
    assert [p.page for p in pages_needing(state, Stage.TYPESET)] == list(range(1, 6))
    assert state.status is ChapterStatus.PENDING


def test_invalidate_without_pages_resets_every_page(populated):
    _, state = populated
    invalidate_from(state, Stage.OCR)
    assert [p.page for p in pages_needing(state, Stage.OCR)] == list(range(1, 6))
    assert state.stage(Stage.OCR).status is StageStatus.PENDING
    assert state.stage(Stage.DETECT).status is StageStatus.DONE


def test_invalidate_clears_a_pending_gate(populated):
    _, state = populated
    open_gate(state, Stage.TRANSLATE, Gate.TRANSLATE, error="bad json")
    invalidate_from(state, Stage.OCR)
    assert state.pending_gate is None
    assert state.gate_error is None


def test_running_marks_done_on_success(populated):
    _, state = populated
    with running(state, Stage.TRANSLATE):
        pass
    rec = state.stage(Stage.TRANSLATE)
    assert rec.status is StageStatus.DONE
    assert rec.started_at and rec.finished_at


def test_running_records_failure_and_reraises(populated):
    _, state = populated
    with pytest.raises(RuntimeError):
        with running(state, Stage.CLEAN):
            raise RuntimeError("inpaint model missing")
    rec = state.stage(Stage.CLEAN)
    assert rec.status is StageStatus.FAILED
    assert "inpaint model missing" in rec.error
    assert state.status is ChapterStatus.FAILED


def test_a_stage_that_opens_a_gate_is_not_marked_done(populated):
    """Halting for the agent is not completion."""
    _, state = populated
    with running(state, Stage.TRANSLATE):
        open_gate(state, Stage.TRANSLATE, Gate.TRANSLATE)
    assert state.status is ChapterStatus.AWAITING_AGENT
    assert state.pending_gate is Gate.TRANSLATE
    assert state.stage(Stage.TRANSLATE).status is StageStatus.PENDING


def test_close_gate_resumes(populated):
    _, state = populated
    open_gate(state, Stage.TRANSLATE, Gate.TRANSLATE, error="boom")
    close_gate(state)
    assert state.pending_gate is None
    assert state.gate_error is None
    assert state.status is ChapterStatus.RUNNING


def test_corrupt_state_file_raises_stateerror(store: ChapterStore):
    store.create()
    store.paths.state_file.write_text("{ not json", encoding="utf-8")
    with pytest.raises(StateError):
        store.load()


def test_missing_state_file_explains_itself(store: ChapterStore):
    with pytest.raises(StateError, match="comic run"):
        store.load()


def test_writes_are_atomic_leaving_no_temp_files(populated):
    store, state = populated
    store.save(state)
    leftovers = [p.name for p in store.paths.workspace.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == []
