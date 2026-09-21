"""Assembling the context handed to the agent at the translate gate.

Not the whole series history -- that would be unreadable and mostly irrelevant.
Only what changes a line's wording (spec 20): who these people are, how they
address each other, which terms are already settled, and what just happened.

Rendered as markdown because the reader is an agent, and a markdown table of
pronouns is easier to honour than nested JSON.
"""

from __future__ import annotations

from comic.memory.profile import Character, SeriesMemory

#: Style modes and what they actually ask for (spec 16).
STYLE_NOTES = {
    "literal": "Stay close to the source. Prefer accuracy over flow.",
    "natural": "Natural Vietnamese. Keep the meaning; let the phrasing breathe.",
    "published_comic": (
        "Write as a published Vietnamese comic would: natural spoken phrasing, "
        "each line short enough to sit in its bubble, voices distinct by "
        "character. Do not translate word for word -- translate what the line "
        "is doing."
    ),
    "localized": (
        "Localize freely into Vietnamese idiom, including cultural references, "
        "as long as the scene still makes sense."
    ),
}


def _character_section(characters: dict[str, Character]) -> str:
    if not characters:
        return (
            "No characters recorded yet. Name whoever you can identify in your "
            "response so later chapters inherit them.\n"
        )

    lines = []
    for name, ch in sorted(characters.items()):
        bits = [f"### {name}"]
        if ch.role:
            bits.append(f"- role: {ch.role}")
        if ch.personality:
            bits.append(f"- personality: {ch.personality}")
        if ch.speech_style:
            bits.append(f"- speech: {ch.speech_style}")
        if ch.aliases:
            bits.append(f"- also called: {', '.join(ch.aliases)}")
        for note in ch.notes:
            bits.append(f"- {note}")
        lines.append("\n".join(bits))
    return "\n\n".join(lines) + "\n"


def _pronoun_table(characters: dict[str, Character]) -> str:
    """The addressing matrix, as a table.

    Vietnamese has no neutral second person, so every line commits to a
    relationship. Letting that drift between chapters is the single most
    noticeable translation failure, which is why it is pinned per ordered pair.
    """
    rows = []
    for name, ch in sorted(characters.items()):
        for other, address in sorted(ch.addresses.items()):
            rows.append(
                f"| {name} | {other} | {address.self_term} | "
                f"{address.other_term} | {address.formality} |"
            )
    if not rows:
        return (
            "No pronoun rules set yet. Choose what fits the scene, keep it "
            "consistent within the chapter, and report every pair you used in "
            "`address_pairs` so later chapters inherit them.\n"
        )
    header = (
        "| speaker | to | calls self | calls them | register |\n"
        "|---|---|---|---|---|\n"
    )
    return header + "\n".join(rows) + "\n"


def build_context(
    series: str,
    chapter: str,
    source_language: str,
    target_language: str,
) -> str:
    """The markdown briefing written alongside a translate request."""
    memory = SeriesMemory(series)
    profile = memory.load_profile()
    characters = memory.load_characters()
    glossary = memory.load_glossary()
    previous = memory.previous_summary(chapter)

    style = STYLE_NOTES.get(profile.style_mode, STYLE_NOTES["published_comic"])

    parts = [
        f"# {profile.name} — {chapter}",
        "",
        f"Translating **{source_language} → {target_language}**.",
        "",
        "## Style",
        "",
        style,
        "",
    ]

    if profile.rules:
        parts += ["### Series rules", ""]
        parts += [f"- {rule}" for rule in profile.rules]
        parts.append("")

    if profile.synopsis:
        parts += ["## Series", "", profile.synopsis, ""]

    if previous:
        parts += [
            f"## Previously ({previous.chapter})",
            "",
            previous.summary,
            "",
        ]

    parts += ["## Characters", "", _character_section(characters), ""]
    parts += ["## Forms of address", "", _pronoun_table(characters), ""]

    if glossary:
        parts += [
            "## Glossary",
            "",
            "Use these renderings. They are context, not find-and-replace: "
            "inflect them to fit the sentence.",
            "",
            "| source | Vietnamese | note |",
            "|---|---|---|",
        ]
        for entry in sorted(glossary, key=lambda e: e.source):
            parts.append(f"| {entry.source} | {entry.target} | {entry.note or ''} |")
        parts.append("")

    parts += [
        "## How to answer",
        "",
        "- One entry per region id. Keep the reading order you are given.",
        "- `fit_width`/`fit_height` are the usable bubble space in pixels. A "
        "line that overruns it will have to be shrunk until it fits, so prefer "
        "the shorter phrasing where two are equally good.",
        "- Say who is speaking when you can tell; it is how the pronoun table "
        "gets built.",
        "- Report the forms of address you used in `address_pairs`, one entry "
        "per ordered (speaker, addressee). Any pair already in the table above "
        "is fixed -- match it rather than re-deciding, because pronouns "
        "drifting between chapters reads as a different relationship.",
        "- Only report pairs for **named, recurring** characters. This table is "
        "inherited by every later chapter, so a one-off role like \"the "
        "culprit\" or \"a bystander\" does not belong in it -- next chapter's "
        "culprit is a different person.",
        "- Report new terms and anything you learned about a character, so the "
        "next chapter inherits it.",
        "",
    ]
    return "\n".join(parts)
