"""Chapter state persistence and the stage graph.

``chapter.json`` is the single source of truth for what has been done. Writes are
atomic (temp file + replace) because a run interrupted mid-write must never leave
an unparseable state file -- that would strand the whole chapter.
"""

from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

from pydantic import BaseModel, ValidationError

from comic.paths import ChapterPaths
from comic.schemas import (
    STAGE_ORDER,
    ChapterState,
    ChapterStatus,
    Gate,
    PageRecord,
    Stage,
    StageStatus,
    utcnow,
)


class StateError(RuntimeError):
    """Raised when state on disk is missing or unusable."""


def write_json(path: Path, model: BaseModel) -> None:
    """Atomically serialise a pydantic model to ``path``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = model.model_dump_json(indent=2, exclude_none=False)
    # Same directory as the target, so os.replace stays on one filesystem.
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def read_json(path: Path, model: type[BaseModel]) -> BaseModel:
    if not path.exists():
        raise StateError(f"missing file: {path}")
    try:
        return model.model_validate_json(path.read_text(encoding="utf-8"))
    except ValidationError as exc:
        raise StateError(f"{path} does not match {model.__name__}:\n{exc}") from exc
    except json.JSONDecodeError as exc:
        raise StateError(f"{path} is not valid JSON: {exc}") from exc


class ChapterStore:
    """Load/save wrapper around one chapter's state file."""

    def __init__(self, paths: ChapterPaths) -> None:
        self.paths = paths

    def exists(self) -> bool:
        return self.paths.state_file.exists()

    def load(self) -> ChapterState:
        if not self.exists():
            raise StateError(
                f"no chapter state at {self.paths.state_file}. "
                f"Run `comic run` for this chapter first."
            )
        return read_json(self.paths.state_file, ChapterState)  # type: ignore[return-value]

    def save(self, state: ChapterState) -> None:
        state.updated_at = utcnow()
        write_json(self.paths.state_file, state)

    def create(self, **kwargs) -> ChapterState:
        state = ChapterState(
            series=self.paths.series,
            chapter=self.paths.chapter,
            **kwargs,
        )
        self.paths.ensure()
        self.save(state)
        return state

    def load_or_create(self, **kwargs) -> ChapterState:
        return self.load() if self.exists() else self.create(**kwargs)

    @contextmanager
    def edit(self) -> Iterator[ChapterState]:
        """Load, hand out for mutation, save on clean exit."""
        state = self.load()
        yield state
        self.save(state)


# ------------------------------------------------------------------ stage graph


def stages_from(stage: Stage) -> tuple[Stage, ...]:
    """The stage and everything downstream of it."""
    idx = STAGE_ORDER.index(stage)
    return STAGE_ORDER[idx:]


def next_stage(state: ChapterState) -> Stage | None:
    """First stage not yet done. ``None`` means the chapter is finished."""
    for stage in STAGE_ORDER:
        if state.stage(stage).status is not StageStatus.DONE:
            return stage
    return None


def invalidate_from(state: ChapterState, stage: Stage, pages: list[int] | None = None) -> None:
    """Mark ``stage`` and everything after it as pending again.

    ``pages`` limits the reset to specific pages, which is what makes
    ``comic rerun --page 17 --from ocr`` touch only page 17's artifacts. Chapter
    level stage records still reset, since the chapter as a whole is no longer
    complete -- but per-page records for untouched pages keep their DONE status,
    so the re-run skips them.
    """
    affected = stages_from(stage)
    for st in affected:
        rec = state.stage(st)
        rec.status = StageStatus.PENDING
        rec.error = None
        rec.finished_at = None

    for page in state.pages:
        if pages is not None and page.page not in pages:
            continue
        for st in affected:
            page.stages[st] = StageStatus.PENDING

    state.status = ChapterStatus.PENDING
    state.pending_gate = None
    state.gate_error = None


def pages_needing(state: ChapterState, stage: Stage) -> list[PageRecord]:
    """Pages where ``stage`` has not completed."""
    return [p for p in state.pages if p.status_of(stage) is not StageStatus.DONE]


@contextmanager
def running(state: ChapterState, stage: Stage) -> Iterator[None]:
    """Mark a stage running, then done or failed depending on the outcome."""
    rec = state.stage(stage)
    rec.status = StageStatus.RUNNING
    rec.started_at = utcnow()
    rec.error = None
    state.status = ChapterStatus.RUNNING
    try:
        yield
    except BaseException as exc:
        rec.status = StageStatus.FAILED
        rec.finished_at = utcnow()
        rec.error = f"{type(exc).__name__}: {exc}"
        state.status = ChapterStatus.FAILED
        raise
    else:
        # A stage that opened a gate is not done; the gate helper set the status.
        if rec.status is StageStatus.RUNNING:
            rec.status = StageStatus.DONE
            rec.finished_at = utcnow()


def open_gate(state: ChapterState, stage: Stage, gate: Gate, error: str | None = None) -> None:
    """Suspend the run until the agent answers."""
    state.status = ChapterStatus.AWAITING_AGENT
    state.pending_gate = gate
    state.gate_error = error
    rec = state.stage(stage)
    rec.status = StageStatus.PENDING


def close_gate(state: ChapterState) -> None:
    state.pending_gate = None
    state.gate_error = None
    state.status = ChapterStatus.RUNNING


def format_duration(start: datetime | None, end: datetime | None) -> str:
    if not start or not end:
        return "-"
    secs = int((end - start).total_seconds())
    if secs < 60:
        return f"{secs}s"
    return f"{secs // 60}m{secs % 60:02d}s"
