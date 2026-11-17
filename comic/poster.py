"""The group's QR poster, set before page 001 of every published chapter.

The operator (2026-10-26): every series we publish opens with
``Ap_phich_Rong_Nho_QR_Facebook.png`` (the fan page's QR code). It is written
as ``000.<ext>``, so the story pages keep their numbers and still match the
source; a project without the file publishes exactly as before.
"""

from __future__ import annotations

from pathlib import Path

from comic.paths import project_root

POSTER_NAME = "Ap_phich_Rong_Nho_QR_Facebook.png"
POSTER_STEM = "000"


def poster_path() -> Path:
    return project_root() / POSTER_NAME


def write_poster(library: Path, fmt: str = "png", quality: int = 95) -> Path | None:
    """Put the poster in ``library`` as ``000.<fmt>``; None when there is none."""
    source = poster_path()
    if not source.exists():
        return None
    from PIL import Image

    target = library / f"{POSTER_STEM}.{fmt}"
    temp = target.with_suffix(target.suffix + ".tmp")
    image = Image.open(source).convert("RGB")
    if fmt == "webp":
        image.save(temp, "WEBP", quality=quality, method=6)
    else:
        image.save(temp, "PNG", optimize=True)
    temp.replace(target)
    return target
