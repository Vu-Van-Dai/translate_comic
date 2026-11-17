"""Build Typer Tools text styles (Photoshop panel) for the operator's hand lettering.

Typer Tools (swirt, CEP panel) keeps its folders and styles in a JSON file,
``%APPDATA%/Adobe/CEP/extensions/typertools/storage``. This writes one folder
per series, holding the styles the pipeline itself uses for that series --
the same faces, case and leading as ``profiles/<series>/fonts.yaml`` -- so a
line the operator types by hand (an SFX, a missed aside) matches the
pipeline's own lettering.

    python scripts/typer_styles.py            # writes typertools/TyperToolsExport.json
    python scripts/typer_styles.py --install  # also writes the panel's storage (backup kept)

Close the Typer Tools panel before ``--install``, or use the panel's own
Settings > Import with the export file: an open panel keeps its state in
memory and overwrites the storage file on its next change.

Sizes are pixels at the series' own page size (``typeUnit: pixelsUnit``),
measured from the renderer's layouts where the series has been rendered and
estimated from its source lettering otherwise. The panel's text-scale box
scales a whole style when a page is larger or smaller than usual.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Photoshop's own PostScript names (app.fonts), which differ from the font
# files' name tables for the faces whose names contain spaces.
FACES = {
    "AnimeAce3": ("000AnimeAce3TeddyBear", "000 AnimeAce3 [TeddyBear]", "Regular"),
    "AnimeAce3 Italic": ("000AnimeAce3BBKyo-Italic", "000 Anime Ace 3 BB [Kyo]", "Italic"),
    "FamousLW": ("000FamousLastWordsTB-BoldIta", "000 FamousLastWords TB", "Bold Italic"),
    "Felt": ("000FeltTeddyBear", "000 Felt [TeddyBear]", "Regular"),
    "Augie": ("000-Augie-VN-[TeddyBear]", "augie", "Regular"),
    "Bangers": ("Bangers-Regular", "Bangers", "Regular"),
    "BeVietnamPro": ("BeVietnamPro-Regular", "Be Vietnam Pro", "Regular"),
}

# Baseline-to-baseline spacing in ems: Vietnamese stacks accents, so each face
# gets the span from Ỗ to Ộ plus 0.06 em, never below 1.22 (fit.leading_em).
LEADING_EM = {
    "AnimeAce3": 1.22, "AnimeAce3 Italic": 1.27, "FamousLW": 1.28, "Felt": 1.38,
    "Augie": 1.34, "Bangers": 1.66, "BeVietnamPro": 1.44,
}

INK = (20, 20, 20)

# (style name, face, all caps, size relative to the dialogue size)
MANGA = [
    ("Thoại + suy nghĩ (AnimeAce3)", "AnimeAce3", True, 1.0),
    ("Dẫn truyện / chú thích (Felt)", "Felt", False, 1.1),
    ("Hét (FamousLW)", "FamousLW", True, 1.15),
    ("Thì thầm (AnimeAce3 nhỏ)", "AnimeAce3", True, 0.68),
    ("Chữ nhỏ ngoài bóng (Felt)", "Felt", False, 0.85),
    ("Tin nhắn / màn hình (BeVietnamPro)", "BeVietnamPro", False, 0.9),
    ("SFX (Bangers)", "Bangers", True, 2.5),
]
MANHWA = [
    ("Thoại (AnimeAce3)", "AnimeAce3", True, 1.0),
    ("Suy nghĩ / dẫn truyện (AnimeAce3 Italic)", "AnimeAce3 Italic", True, 1.0),
    ("Hét (FamousLW)", "FamousLW", True, 1.15),
    ("Nhẹ nhàng / chữ nhỏ ngoài bóng (Augie)", "Augie", False, 0.95),
    ("Thì thầm (AnimeAce3 nhỏ)", "AnimeAce3", True, 0.68),
    ("Tin nhắn / màn hình (BeVietnamPro)", "BeVietnamPro", False, 0.9),
    ("SFX (Bangers)", "Bangers", True, 2.5),
]
# Super Lovers sets its captions in Augie (profiles/Super Lovers/fonts.yaml).
SUPER_LOVERS = [
    s if not s[0].startswith(("Dẫn truyện", "Chữ nhỏ")) else (s[0].replace("Felt", "Augie"), "Augie", s[2], s[3])
    for s in MANGA
]

# Dialogue size in px. Measured = median of the renderer's own layouts on
# ch.1 (scratch measurement 2026-10-27); estimated = source lettering size
# x 0.9 (English sources) or page width x the measured ratio of a sibling
# series (Chinese sources, whose vertical CJK lettering says little about
# the Latin size).
SERIES = {
    "Dare mo Shiranai Kimi no Koto": (MANGA, 39),       # measured, 1920 px pages
    "Kimi ga Tomoshite Kureta nara": (MANGA, 22),       # measured, 801 px
    "Itsuka, Kazoku ni Naru made": (MANGA, 30),         # measured, 1100 px
    "Kimigazer": (MANGA, 21),                           # measured, 844 px
    "Shuumatsu, Kimi to": (MANGA, 22),                  # measured, 950 px
    "Super Lovers": (SUPER_LOVERS, 21),                 # measured, 978 px
    "Kiss Shite Sugar-kun": (MANGA, 18),                # estimated, 683 px
    "Ju Jue Lian Lian Yi Wei Ai": (MANGA, 23),          # estimated, 801 px
    "Kawaii Kimi ni Koiwazurai": (MANGA, 19),           # estimated, 651 px
    "Ku Ku Lian Nan You": (MANGA, 23),                  # estimated, 801 px
    "When I Tried to Enjoy the Life of a Neglected Husband": (MANGA, 30),  # estimated, 1440 px
    "Dasadanan": (MANHWA, 25),                          # measured, 690 px
    "Golden Light for You": (MANHWA, 46),               # estimated, 1280 px
    "My Insatiable Demon": (MANHWA, 46),                # estimated, 1280 px
    "Walking in the Rain": (MANHWA, 24),                # estimated, 850 px
    "Destined to Meet on the Trends": (MANHWA, 24),     # estimated, 700 px
    "Benming Shi Qingmei Zhuma": (MANHWA, 27),          # estimated, 720 px
    "Xiao Gou Diu Le Hun": (MANHWA, 40),                # estimated, 1087 px
}


def stable_id(*parts: str) -> str:
    """An id that stays the same across runs, so a re-import replaces a style."""
    import hashlib

    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:8]


def text_props(face: str, caps: bool, size: int) -> dict:
    ps, family, style = FACES[face]
    leading = round(size * LEADING_EM[face])
    return {
        "layerText": {
            "textGridding": "none",
            "orientation": "horizontal",
            "antiAlias": "antiAliasSmooth",
            "textStyleRange": [{
                "from": 0, "to": 100,
                "textStyle": {
                    "fontPostScriptName": ps, "fontName": family, "fontStyleName": style,
                    "fontScript": 0, "fontTechnology": 1, "fontAvailable": True,
                    "size": size, "impliedFontSize": size,
                    "horizontalScale": 100, "verticalScale": 100,
                    "autoLeading": False, "leading": leading, "impliedLeading": leading,
                    "tracking": 0, "baselineShift": 0, "impliedBaselineShift": 0,
                    "autoKern": "metricsKern",
                    "fontCaps": "allCaps" if caps else "normal",
                    "digitSet": "defaultDigits", "markYDistFromBaseline": 100,
                    "otbaseline": "normal", "ligature": False, "altligature": False,
                    "connectionForms": False, "contextualLigatures": False,
                    "baselineDirection": "withStream",
                    "color": {"red": INK[0], "green": INK[1], "blue": INK[2]},
                },
            }],
            "paragraphStyleRange": [{
                "from": 0, "to": 100,
                "paragraphStyle": {
                    "burasagari": "burasagariNone",
                    "singleWordJustification": "justifyAll",
                    "justificationMethodType": "justifMethodAutomatic",
                    "textEveryLineComposer": False,
                    "alignment": "center",
                    "hangingRoman": True,
                    # Never break a Vietnamese word: a split syllable can
                    # orphan a diacritic onto its own line.
                    "hyphenate": False,
                },
            }],
        },
        "typeUnit": "pixelsUnit",
    }


def build() -> dict:
    now = int(time.time() * 1000)
    folders, styles = [], []
    for series, (kinds, dialogue_px) in SERIES.items():
        folder_id = stable_id("folder", series)
        folders.append({"id": folder_id, "name": series})
        for name, face, caps, scale in kinds:
            size = max(6, round(dialogue_px * scale))
            styles.append({
                "id": stable_id("style", series, name),
                "name": name,
                "folder": folder_id,
                "textProps": text_props(face, caps, size),
                "prefixes": [],
                "prefixColor": "#FFF3B0",
                "edited": now,
            })
    return {"folders": folders, "styles": styles}


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--install", action="store_true",
                        help="also write the panel's storage file (close the panel first)")
    args = parser.parse_args()

    data = build()
    out = ROOT / "typertools" / "TyperToolsExport.json"
    out.parent.mkdir(exist_ok=True)
    export = {"ignoreLinePrefixes": ["##"], "defaultStyleId": None, **data,
              "version": "1.4.9", "exported": time.strftime("%Y-%m-%dT%H:%M:%S")}
    out.write_text(json.dumps(export, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(data['folders'])} folders, {len(data['styles'])} styles -> {out}")

    if args.install:
        storage = Path(os.environ["APPDATA"]) / "Adobe" / "CEP" / "extensions" / "typertools" / "storage"
        current = json.loads(storage.read_text(encoding="utf-8")) if storage.exists() else {}
        backup = storage.with_name(f"storage.bak-{time.strftime('%Y%m%d-%H%M%S')}")
        if storage.exists():
            shutil.copy2(storage, backup)
        ours = {f["id"] for f in data["folders"]}
        mine = {s["id"] for s in data["styles"]}
        # Keep anything the operator made by hand; replace only our own ids.
        current["folders"] = [f for f in current.get("folders", []) if f["id"] not in ours] + data["folders"]
        current["styles"] = [s for s in current.get("styles", []) if s["id"] not in mine] + data["styles"]
        tmp = storage.with_name("storage.tmp")
        tmp.write_text(json.dumps(current, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, storage)
        print(f"installed -> {storage} (backup {backup.name})")


if __name__ == "__main__":
    main()
