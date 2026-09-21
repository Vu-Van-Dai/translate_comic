"""Environment probing.

``comic doctor`` answers one question: can this machine run a chapter end to end,
and if not, exactly which command fixes it? Nothing here installs anything -- a
diagnostic that mutates the system is a diagnostic you stop trusting.
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from comic.paths import browser_profile_dir, fonts_dir, models_dir, project_root
from comic.typesetting.coverage import check_directory


class Level(str, Enum):
    OK = "ok"
    WARN = "warn"     # degraded, pipeline still runs
    FAIL = "fail"     # a stage cannot run at all


@dataclass
class Check:
    name: str
    level: Level
    detail: str
    fix: str | None = None


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)

    def add(self, name: str, level: Level, detail: str, fix: str | None = None) -> None:
        self.checks.append(Check(name, level, detail, fix))

    @property
    def failures(self) -> list[Check]:
        return [c for c in self.checks if c.level is Level.FAIL]

    @property
    def warnings(self) -> list[Check]:
        return [c for c in self.checks if c.level is Level.WARN]

    @property
    def ready(self) -> bool:
        return not self.failures


def _module_version(name: str) -> str | None:
    """Import-free presence check, then a cheap version read if it is there."""
    if importlib.util.find_spec(name) is None:
        return None
    try:
        from importlib.metadata import PackageNotFoundError, version
        dist = _DIST_NAMES.get(name, name)
        try:
            return version(dist)
        except PackageNotFoundError:
            return "present"
    except Exception:
        return "present"


#: Import name -> distribution name, where they differ.
_DIST_NAMES = {
    "cv2": "opencv-python-headless",
    "yaml": "PyYAML",
    "PIL": "pillow",
    "fontTools": "fonttools",
    "manga_ocr": "manga-ocr",
    "rapidocr_onnxruntime": "rapidocr-onnxruntime",
}

#: (import name, human label, install extra, required?)
_CORE = [
    ("pydantic", "pydantic", None, True),
    ("typer", "typer", None, True),
    ("rich", "rich", None, True),
    ("yaml", "PyYAML", None, True),
    ("PIL", "pillow", None, True),
    ("numpy", "numpy", None, True),
    ("fontTools", "fonttools", None, True),
]

_OPTIONAL = [
    ("playwright", "playwright", "browser", "download stage cannot run"),
    ("cv2", "opencv-python-headless", "vision", "detect/clean stages cannot run"),
    ("shapely", "shapely", "vision", "bubble-fitting falls back to bbox only"),
    ("scipy", "scipy", "vision", "mask refinement degraded"),
    ("onnxruntime", "onnxruntime", "vision", "ONNX detector/inpainter cannot run"),
    ("manga_ocr", "manga-ocr", "ocr", "Japanese OCR unavailable"),
    ("rapidocr_onnxruntime", "rapidocr-onnxruntime", "ocr", "Chinese/English OCR unavailable"),
]

#: Weights fetched by ``scripts/fetch_models.py``.
_MODELS = [
    ("comictextdetector.onnx", True, "text detection + mask"),
    ("inpainting_lama.onnx", False, "tier-2 inpainting (falls back to tier 1)"),
    ("korean_PP-OCRv5_rec.onnx", False, "Korean OCR (the review gate covers it otherwise)"),
    ("korean_PP-OCRv5_rec_keys.txt", False, "the charset Korean OCR decodes into"),
]


def check_python(report: Report) -> None:
    v = sys.version_info
    label = f"{v.major}.{v.minor}.{v.micro}"
    if v < (3, 11):
        report.add("python", Level.FAIL, f"{label} (need >= 3.11)", "install Python 3.11+")
    else:
        report.add("python", Level.OK, label)


def check_core(report: Report) -> None:
    missing = []
    for mod, label, _extra, _req in _CORE:
        ver = _module_version(mod)
        if ver is None:
            missing.append(label)
        else:
            report.add(f"dep:{label}", Level.OK, ver)
    if missing:
        report.add(
            "core deps", Level.FAIL, f"missing: {', '.join(missing)}",
            "pip install -e .",
        )


def check_optional(report: Report) -> None:
    for mod, label, extra, consequence in _OPTIONAL:
        ver = _module_version(mod)
        if ver is None:
            report.add(
                f"dep:{label}", Level.FAIL, f"not installed -- {consequence}",
                f"pip install -e .[{extra}]",
            )
        else:
            report.add(f"dep:{label}", Level.OK, ver)


def check_browser(report: Report) -> None:
    """Playwright needs both the package and a downloaded browser binary."""
    if importlib.util.find_spec("playwright") is None:
        report.add("browser:chromium", Level.FAIL, "playwright not installed",
                   "pip install -e .[browser] && python -m playwright install chromium")
        return
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            path = Path(pw.chromium.executable_path)
        if path.exists():
            report.add("browser:chromium", Level.OK, str(path.parent.name))
        else:
            report.add("browser:chromium", Level.FAIL, "browser binary not downloaded",
                       "python -m playwright install chromium")
    except Exception as exc:
        report.add("browser:chromium", Level.FAIL, f"unusable: {type(exc).__name__}: {exc}",
                   "python -m playwright install chromium")

    profile = browser_profile_dir()
    if profile.exists() and any(profile.iterdir()):
        report.add("browser:profile", Level.OK, f"persistent profile at {profile.name}")
    else:
        report.add("browser:profile", Level.WARN,
                   "no saved profile yet -- login-gated sites (bilibili) will need "
                   "a one-time interactive sign-in",
                   "comic login --site manga.bilibili.com")


def check_models(report: Report) -> None:
    root = models_dir()
    for filename, required, purpose in _MODELS:
        path = root / filename
        if path.exists() and path.stat().st_size > 0:
            mb = path.stat().st_size / 1_048_576
            report.add(f"model:{filename}", Level.OK, f"{mb:.1f} MB")
        else:
            level = Level.FAIL if required else Level.WARN
            report.add(f"model:{filename}", level, f"missing -- {purpose}",
                       "python scripts/fetch_models.py")


def check_fonts(report: Report) -> None:
    """A font that cannot render Vietnamese is worse than no font: it renders tofu."""
    directory = fonts_dir()
    results = check_directory(directory)
    if not results:
        report.add("fonts", Level.FAIL, f"no fonts in {directory}",
                   "python scripts/fetch_fonts.py")
        return
    good = [r for r in results if r.ok]
    bad = [r for r in results if not r.ok]
    for r in bad:
        report.add(f"font:{r.family}", Level.WARN,
                   f"incomplete Vietnamese coverage, {len(r.missing)} glyphs missing",
                   "remove it or replace with a Vietnamese-subset face")
    if good:
        report.add("fonts", Level.OK,
                   f"{len(good)} usable: {', '.join(r.family for r in good[:4])}"
                   + (" ..." if len(good) > 4 else ""))
    else:
        report.add("fonts", Level.FAIL,
                   f"{len(results)} font(s) present but none cover Vietnamese",
                   "python scripts/fetch_fonts.py")


def check_external_tools(report: Report) -> None:
    """Optional image editors. Absence is fine -- the built-in tiers cover it."""
    if shutil.which("magick") or shutil.which("convert"):
        report.add("tool:imagemagick", Level.OK, "available")
    else:
        report.add("tool:imagemagick", Level.WARN,
                   "not found -- built-in Pillow/OpenCV path is used instead")

    ps = any(
        (Path(base) / "Adobe").exists()
        and any(p.name.startswith("Adobe Photoshop") for p in (Path(base) / "Adobe").iterdir())
        for base in (r"C:\Program Files", r"C:\Program Files (x86)")
        if (Path(base) / "Adobe").exists()
    )
    if ps:
        report.add("tool:photoshop", Level.OK, "detected -- COM backend can be enabled")
    else:
        report.add("tool:photoshop", Level.WARN,
                   "not installed -- cleaning uses the built-in three-tier path")


def check_layout(report: Report) -> None:
    root = project_root()
    missing = [d for d in ("library", "workspace", "profiles", "assets", "models")
               if not (root / d).exists()]
    if missing:
        report.add("layout", Level.FAIL, f"missing directories: {', '.join(missing)}",
                   "python scripts/bootstrap.py")
    else:
        report.add("layout", Level.OK, str(root))


def run_checks() -> Report:
    report = Report()
    check_python(report)
    check_layout(report)
    check_core(report)
    check_optional(report)
    check_browser(report)
    check_models(report)
    check_fonts(report)
    check_external_tools(report)
    return report
