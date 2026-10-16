"""Editing styles (``profiles/_presets/video_styles.yaml``) and a series' video rules."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from comic.paths import SeriesPaths, project_root


@dataclass
class Style:
    name: str
    about: str
    shot_seconds: tuple[float, float]
    motions: list[str]
    strength: float
    transition: str
    transition_seconds: float
    effects: list[str]
    text: dict
    music: list[str]
    adults_only: bool = False


def styles() -> dict[str, Style]:
    f = project_root() / "profiles" / "_presets" / "video_styles.yaml"
    data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
    out = {}
    for name, v in data.items():
        out[name] = Style(
            name=name, about=v.get("about", ""), shot_seconds=tuple(v.get("shot_seconds", (2.5, 3.5))),
            motions=v.get("motions", ["static"]), strength=float(v.get("strength", 0.4)),
            transition=v.get("transition", "cut"), transition_seconds=float(v.get("transition_seconds", 0.4)),
            effects=v.get("effects", []), text=v.get("text", {}), music=v.get("music", []),
            adults_only=bool(v.get("adults_only", False)),
        )
    return out


@dataclass
class SeriesVideo:
    """``profiles/<series>/video.yaml`` -- what this series' videos must respect."""

    rtl: bool = True
    hashtags: list[str] = field(default_factory=list)
    #: Styles this series may not use (e.g. romantic while a lead is a minor).
    forbid_styles: list[str] = field(default_factory=list)
    #: Character -> why they count as a minor in these chapters.
    minors: dict[str, str] = field(default_factory=dict)
    notes: str = ""
    #: The operator allows our finished Vietnamese pages in this series' videos.
    allow_translated: bool = False


def series_video(series: str) -> SeriesVideo:
    f: Path = SeriesPaths(series).profile_dir / "video.yaml"
    if not f.exists():
        return SeriesVideo()
    data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
    known = SeriesVideo.__dataclass_fields__
    return SeriesVideo(**{k: v for k, v in data.items() if k in known})
