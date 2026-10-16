"""Download the default OFL font set and gate each face on Vietnamese coverage.

Fail-closed: a downloaded face that cannot render the full Vietnamese repertoire
is deleted rather than kept, because a partially-covering font does not error at
render time -- it silently emits tofu in the middle of a sentence.

Verified empirically on this machine: every handwriting/comic face shipped with
Windows (Comic Sans, Segoe Print, Segoe Script) fails this check, and Bahnschrift
fails by a single glyph. Hence the download.

Usage:
    python scripts/fetch_fonts.py [--force]
"""

from __future__ import annotations

import argparse
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from comic.console import setup_console, console  # noqa: E402
from comic.paths import fonts_dir  # noqa: E402
from comic.typesetting.coverage import check_font  # noqa: E402

GOOGLE_FONTS = "https://raw.githubusercontent.com/google/fonts/main/ofl"

#: (remote path, local filename, role note). Roles are wired up in fonts.yaml;
#: this list only decides what lands on disk.
MANIFEST: list[tuple[str, str, str]] = [
    ("bevietnampro/BeVietnamPro-Regular.ttf", "BeVietnamPro-Regular.ttf", "dialogue"),
    ("bevietnampro/BeVietnamPro-Bold.ttf", "BeVietnamPro-Bold.ttf", "dialogue bold"),
    ("bevietnampro/BeVietnamPro-Italic.ttf", "BeVietnamPro-Italic.ttf", "thought"),
    ("bevietnampro/BeVietnamPro-BoldItalic.ttf", "BeVietnamPro-BoldItalic.ttf", "emphasis"),
    ("bevietnampro/BeVietnamPro-ExtraBold.ttf", "BeVietnamPro-ExtraBold.ttf", "shout"),
    ("anton/Anton-Regular.ttf", "Anton-Regular.ttf", "shout display"),
    ("lora/Lora%5Bwght%5D.ttf", "Lora-Variable.ttf", "narration serif"),
    ("nunito/Nunito%5Bwght%5D.ttf", "Nunito-Variable.ttf", "caption"),
    # Comic lettering. Every one of these was checked against the Vietnamese
    # repertoire before being listed -- Comic Neue, the obvious choice, fails
    # with 98 missing glyphs, and Fredoka and Titan One fail too. A display
    # face that looks right and renders tofu is worse than a plain one.
    ("bangers/Bangers-Regular.ttf", "Bangers-Regular.ttf", "narration / shout caps"),
    ("mali/Mali-Regular.ttf", "Mali-Regular.ttf", "dialogue handwriting"),
    ("mali/Mali-Italic.ttf", "Mali-Italic.ttf", "thought handwriting"),
    ("patrickhand/PatrickHand-Regular.ttf", "PatrickHand-Regular.ttf", "dialogue alt"),
    ("baloo2/Baloo2%5Bwght%5D.ttf", "Baloo2-Variable.ttf", "caption rounded"),
]

USER_AGENT = "comic-translator/0.1 (+font bootstrap)"


def download(url: str, dest: Path, timeout: int = 60) -> int:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = response.read()
    if len(data) < 1024:
        raise ValueError(f"suspiciously small download ({len(data)} bytes)")
    dest.write_bytes(data)
    return len(data)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="Re-download existing fonts.")
    args = parser.parse_args()

    setup_console()
    target = fonts_dir()
    target.mkdir(parents=True, exist_ok=True)

    installed, skipped, rejected, failed = 0, 0, 0, 0

    for remote, filename, role in MANIFEST:
        dest = target / filename
        if dest.exists() and not args.force:
            console.print(f"[path]  skip[/path] {filename} (already present)")
            skipped += 1
            continue

        url = f"{GOOGLE_FONTS}/{remote}"
        try:
            size = download(url, dest)
        except (urllib.error.URLError, ValueError, OSError) as exc:
            console.print(f"[fail]  fail[/fail] {filename}: {exc}")
            failed += 1
            continue

        # Gate on coverage before the font is allowed to stay.
        try:
            result = check_font(dest)
        except ValueError as exc:
            dest.unlink(missing_ok=True)
            console.print(f"[fail]reject[/fail] {filename}: unreadable ({exc})")
            rejected += 1
            continue

        if result.ok:
            console.print(
                f"[ok]    ok[/ok] {filename} ({size // 1024} KB) -- {role}"
            )
            installed += 1
        else:
            dest.unlink(missing_ok=True)
            console.print(
                f"[fail]reject[/fail] {filename}: {len(result.missing)} Vietnamese "
                f"glyphs missing -- deleted"
            )
            rejected += 1

    console.print()
    console.print(
        f"installed {installed}, skipped {skipped}, rejected {rejected}, failed {failed}"
    )
    console.print(f"[path]{target}[/path]")
    return 1 if (failed or rejected) and installed == 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
