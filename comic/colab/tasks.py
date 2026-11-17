"""Local wrappers that turn pipeline work into Colab jobs and back."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from comic.colab.jobs import ColabError, outputs, remove, submit, wait
from comic.paths import ChapterPaths, project_root

#: Profile files larger than this are not needed for detection or OCR.
PROFILE_MAX_BYTES = 5_000_000


def _tree(base: Path, prefix: str, files: dict[str, Path]) -> None:
    if base.is_dir():
        for f in base.rglob("*"):
            if f.is_file():
                files[f"{prefix}/{f.relative_to(base).as_posix()}"] = f


def analyze(series: str, chapter: str, pages: list[int] | None = None, force: bool = False,
            language: str | None = None, progress=print) -> dict:
    """Run slice + detect + OCR for one chapter on Colab; write results locally."""
    paths = ChapterPaths(series, chapter)
    if not paths.state_file.exists():
        raise ColabError(f"{paths.series} / {paths.chapter} has no chapter.json -- download it first")
    prefix = f"workspace/{paths.series}/{paths.chapter}"
    files: dict[str, Path] = {f"{prefix}/chapter.json": paths.state_file}
    for sub in ("original", "pages", "analysis", "masks"):
        _tree(paths.workspace / sub, f"{prefix}/{sub}", files)
    profiles = project_root() / "profiles"
    for base in (profiles / paths.series, profiles / "_presets"):
        if base.is_dir():
            for f in base.rglob("*"):
                if f.is_file() and f.stat().st_size <= PROFILE_MAX_BYTES:
                    files[f"profiles/{f.relative_to(profiles).as_posix()}"] = f

    progress(f"uploading {len(files)} files to Drive ...")
    job = submit("analyze", files, {
        "series": paths.series, "chapter": paths.chapter, "pages": pages,
        "force": force, "language": language,
    })
    progress(f"job {job.id} posted; waiting for the Colab worker ...")
    done = wait(job, progress=progress)

    out = outputs(job) / prefix
    for f in out.rglob("*"):
        if f.is_file():
            target = paths.workspace / f.relative_to(out)
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name(f".{target.name}.colab")
            shutil.copyfile(f, tmp)
            tmp.replace(target)
    remove(job)
    summary = done.get("summary", {})
    summary["seconds"] = done.get("seconds")
    return summary


def inpaint(items: list[tuple[str, "object", "object"]], progress=print) -> dict:
    """LaMa on Colab. ``items`` are (name, RGB uint8 array, bool/uint8 mask).

    Returns name -> RGB array. Pixels outside each mask are the input's.
    """
    import numpy as np
    from PIL import Image

    if not items:
        return {}
    with tempfile.TemporaryDirectory(prefix="comic_inpaint_") as tmp:
        tmp = Path(tmp)
        files: dict[str, Path] = {}
        specs = []
        for name, image, mask in items:
            img_f, msk_f = tmp / f"{name}.png", tmp / f"{name}_mask.png"
            Image.fromarray(np.asarray(image, dtype=np.uint8)).save(img_f)
            Image.fromarray((np.asarray(mask) > 0).astype(np.uint8) * 255).save(msk_f)
            files[img_f.name], files[msk_f.name] = img_f, msk_f
            specs.append({"name": name, "image": img_f.name, "mask": msk_f.name})
        job = submit("inpaint", files, {"items": specs})
        progress(f"inpaint job {job.id}: {len(items)} item(s) posted")
        wait(job, progress=progress)
        result = {s["name"]: np.array(Image.open(outputs(job) / f"{s['name']}.png").convert("RGB"))
                  for s in specs}
        remove(job)
        return result

