"""Per-series knowledge that every new chapter inherits.

A series is a long-lived workspace, not a pile of unrelated URLs (spec 6). What
lives here is the stuff that must stay consistent across chapters and that no
amount of cleverness can recover from the images alone: who the characters are,
how they address each other, and which terms were already settled.

Pronouns get their own structure because Vietnamese forces a choice on every
line -- there is no neutral "you" -- and a drifting choice reads as a different
relationship. Pinning them per ordered pair is the only way to keep a series
coherent (spec 17).
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml
from pydantic import Field

from comic.paths import SeriesPaths
from comic.schemas import Base, ReadingDirection, utcnow


class Address(Base):
    """How one character speaks to another.

    ``self_term`` is what the speaker calls themselves, ``other_term`` what they
    call the listener: in Vietnamese the pair moves together (anh/em, tôi/cậu)
    and splitting them produces sentences no one would say.
    """

    self_term: str
    other_term: str
    formality: str = "neutral"
    note: str | None = None


class Character(Base):
    name: str
    aliases: list[str] = Field(default_factory=list)
    role: str | None = None
    personality: str | None = None
    speech_style: str | None = None
    #: name -> how this character addresses them.
    addresses: dict[str, Address] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)


class GlossaryEntry(Base):
    source: str
    target: str
    note: str | None = None
    #: Terms, names and honorifics behave differently when translating.
    category: str = "term"


class FontProfile(Base):
    dialogue: str = "BeVietnamPro-Regular.ttf"
    dialogue_bold: str = "BeVietnamPro-Bold.ttf"
    thought: str = "BeVietnamPro-Italic.ttf"
    narration: str = "Lora-Variable.ttf"
    shout: str = "Anton-Regular.ttf"
    caption: str = "Nunito-Variable.ttf"


class SeriesProfile(Base):
    name: str
    source_language: str = "ja"
    target_language: str = "vi"
    reading_direction: ReadingDirection = ReadingDirection.RTL
    #: literal | natural | published_comic | localized  (spec 16)
    style_mode: str = "published_comic"
    synopsis: str | None = None
    #: Free-form rules the agent should honour, e.g. "keep honorifics".
    rules: list[str] = Field(default_factory=list)
    fonts: FontProfile = Field(default_factory=FontProfile)
    #: Leave SFX in the artwork unless the operator says otherwise (spec 27).
    translate_sfx: bool = False


class ChapterSummary(Base):
    chapter: str
    summary: str
    characters: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=lambda: utcnow().isoformat())


class SeriesMemory:
    """Reads and writes everything under ``profiles/<series>/``."""

    def __init__(self, series: str) -> None:
        self.paths = SeriesPaths(series)

    # -- profile -----------------------------------------------------------
    def load_profile(self) -> SeriesProfile:
        path = self.paths.profile_file
        if not path.exists():
            return SeriesProfile(name=self.paths.series)
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        data.setdefault("name", self.paths.series)
        return SeriesProfile.model_validate(data)

    def save_profile(self, profile: SeriesProfile) -> None:
        self.paths.ensure()
        self.paths.profile_file.write_text(
            yaml.safe_dump(
                json.loads(profile.model_dump_json()),
                allow_unicode=True, sort_keys=False,
            ),
            encoding="utf-8",
        )

    # -- characters --------------------------------------------------------
    def load_characters(self) -> dict[str, Character]:
        path = self.paths.characters_file
        if not path.exists():
            return {}
        raw = json.loads(path.read_text(encoding="utf-8"))
        return {k: Character.model_validate(v) for k, v in raw.items()}

    def save_characters(self, characters: dict[str, Character]) -> None:
        self.paths.ensure()
        self.paths.characters_file.write_text(
            json.dumps(
                {k: json.loads(v.model_dump_json()) for k, v in characters.items()},
                ensure_ascii=False, indent=2,
            ),
            encoding="utf-8",
        )

    # -- glossary ----------------------------------------------------------
    def load_glossary(self) -> list[GlossaryEntry]:
        path = self.paths.glossary_file
        if not path.exists():
            return []
        raw = json.loads(path.read_text(encoding="utf-8"))
        return [GlossaryEntry.model_validate(item) for item in raw]

    def save_glossary(self, entries: list[GlossaryEntry]) -> None:
        self.paths.ensure()
        self.paths.glossary_file.write_text(
            json.dumps(
                [json.loads(e.model_dump_json()) for e in entries],
                ensure_ascii=False, indent=2,
            ),
            encoding="utf-8",
        )

    def merge_glossary(self, new: list[GlossaryEntry]) -> int:
        """Add unseen terms. Existing ones win: consistency beats novelty."""
        existing = self.load_glossary()
        known = {e.source for e in existing}
        added = [e for e in new if e.source not in known]
        if added:
            self.save_glossary(existing + added)
        return len(added)

    # -- summaries ---------------------------------------------------------
    def load_summaries(self) -> list[ChapterSummary]:
        path = self.paths.summaries_file
        if not path.exists():
            return []
        raw = json.loads(path.read_text(encoding="utf-8"))
        return [ChapterSummary.model_validate(item) for item in raw]

    def save_summary(self, summary: ChapterSummary) -> None:
        self.paths.ensure()
        summaries = [s for s in self.load_summaries() if s.chapter != summary.chapter]
        summaries.append(summary)
        summaries.sort(key=lambda s: s.chapter)
        self.paths.summaries_file.write_text(
            json.dumps(
                [json.loads(s.model_dump_json()) for s in summaries],
                ensure_ascii=False, indent=2,
            ),
            encoding="utf-8",
        )

    def previous_summary(self, chapter: str) -> ChapterSummary | None:
        earlier = [s for s in self.load_summaries() if s.chapter < chapter]
        return earlier[-1] if earlier else None

    def ensure_initialised(self, profile: SeriesProfile | None = None) -> SeriesProfile:
        """Create the profile directory with defaults on first use."""
        if self.paths.profile_file.exists():
            return self.load_profile()
        created = profile or SeriesProfile(name=self.paths.series)
        self.save_profile(created)
        if not self.paths.characters_file.exists():
            self.save_characters({})
        if not self.paths.glossary_file.exists():
            self.save_glossary([])
        return created
