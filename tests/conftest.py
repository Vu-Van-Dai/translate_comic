from __future__ import annotations

from pathlib import Path

import pytest

from comic.paths import ChapterPaths, SeriesPaths


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the project roots at a temp dir via COMIC_HOME."""
    monkeypatch.setenv("COMIC_HOME", str(tmp_path))
    for sub in ("library", "workspace", "profiles", "models", "assets/fonts"):
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)
    return tmp_path


@pytest.fixture
def chapter(home: Path) -> ChapterPaths:
    paths = ChapterPaths("Paws & Claws", 69)
    paths.ensure()
    return paths


@pytest.fixture
def series(home: Path) -> SeriesPaths:
    paths = SeriesPaths("Paws & Claws")
    paths.ensure()
    return paths
