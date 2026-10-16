"""Making the series' fonts visible to Photoshop.

The pipeline draws through PIL straight from ``assets/fonts/``; Photoshop
cannot. It only sees fonts Windows has installed, and it reads that list once,
at startup. So a layer naming ``Mali-Regular`` silently falls back to Myriad
until the face is installed and Photoshop restarted.

Installing is per-user -- ``%LOCALAPPDATA%\\Microsoft\\Windows\\Fonts`` plus an
``HKCU`` registry entry -- so it needs no administrator rights and touches
nothing system-wide. Removing the file and the value undoes it.

Photoshop addresses a face by its PostScript name (name-table ID 6), not by
its filename or family name, so that is what the plans carry.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

#: Where Windows keeps per-user fonts (Windows 10 1809 and later).
USER_FONTS_DIR = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Windows" / "Fonts"
_REG_PATH = r"Software\Microsoft\Windows NT\CurrentVersion\Fonts"


@dataclass(frozen=True)
class FontInfo:
    file: Path
    postscript: str
    full_name: str


@lru_cache(maxsize=64)
def font_info(path: Path) -> FontInfo:
    """PostScript and full names of a font file.

    For a variable font these are the *default instance*, which is also the
    instance PIL renders when no axis is set -- so both renderers use the
    same face.
    """
    from fontTools.ttLib import TTFont

    names = TTFont(str(path), lazy=True)["name"]
    # A PostScript name may not contain spaces, and Photoshop addresses a
    # face that breaks the rule by its name with each space made a hyphen:
    # the TeddyBear conversion "000 Clairvoyant BB [TeddyBear]" is
    # "000-Clairvoyant-BB-[TeddyBear]" to Photoshop.
    postscript = (names.getDebugName(6) or path.stem).strip().replace(" ", "-")
    full = names.getDebugName(4) or postscript
    return FontInfo(Path(path), postscript, full)


def is_installed(info: FontInfo) -> bool:
    """Whether Windows has this face, per-user or system-wide."""
    if (USER_FONTS_DIR / info.file.name).exists():
        return True
    system = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / info.file.name
    return system.exists()


def install_for_user(info: FontInfo) -> bool:
    """Install one face for the current user. Returns False if already there."""
    import winreg

    if is_installed(info):
        return False
    USER_FONTS_DIR.mkdir(parents=True, exist_ok=True)
    target = USER_FONTS_DIR / info.file.name
    shutil.copy2(info.file, target)
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _REG_PATH) as key:
        winreg.SetValueEx(key, f"{info.full_name} (TrueType)", 0, winreg.REG_SZ, str(target))
    return True
