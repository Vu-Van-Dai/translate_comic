"""The Colab end of the Drive job queue; see :mod:`comic.colab.jobs`.

Runs inside the notebook (``colab/comic_gpu.ipynb``), never locally. It
polls ``My Drive/comic_worker/jobs`` and handles:

``analyze``  the project's own slice + detect + OCR on a copy of the chapter,
             so the result is exactly what ``comic analyze`` would produce,
             only on the T4 (``COMIC_ONNX_PROVIDER=cuda``).
``inpaint``  Anime/Manga Big-LaMa over an image + mask, cropped to the mask
             with a margin; pixels outside the mask are returned untouched.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
import traceback
from pathlib import Path

MODELS = Path("/content/models")
SCRATCH = Path("/content/jobs")
LAMA_FILE = "anime_manga_lama.pt"
#: Longest side LaMa sees; larger crops are scaled down for inference only.
LAMA_MAX_SIDE = 4096   # native size in practice: the T4 has the VRAM (2026-11-13); OOM falls back smaller

_state = {"busy": None}


def environment() -> dict:
    """Library versions, reported with every job: OCR results only match the
    PC's when these do (a mixed OpenCV 4/5 install on Colab read English
    noticeably worse than the PC, 2026-11-08)."""
    import importlib.metadata as md
    import sys

    out = {"python": sys.version.split()[0]}
    for dist in ("numpy", "opencv-python", "opencv-python-headless", "onnxruntime-gpu",
                 "rapidocr-onnxruntime", "manga-ocr", "pillow", "torch", "shapely", "scipy"):
        try:
            out[dist] = md.version(dist)
        except md.PackageNotFoundError:
            pass
    try:
        import cv2

        out["cv2_loaded"] = cv2.__version__
    except ImportError:
        pass
    return out


# ------------------------------------------------------------------ helpers
def _arrived(job_dir: Path, files: dict[str, int]) -> bool:
    for rel, size in files.items():
        f = job_dir / "in" / rel
        try:
            if f.stat().st_size != size:
                return False
        except OSError:
            return False
    return True


def _beat(root: Path, stop: threading.Event) -> None:
    import torch

    gpu = torch.cuda.get_device_name(0) if torch.cuda.is_available() else "none"
    while not stop.is_set():
        try:
            tmp = root / "worker.json.tmp"
            tmp.write_text(json.dumps({"t": time.time(), "gpu": gpu, "busy": _state["busy"]}))
            tmp.replace(root / "worker.json")
        except OSError:
            pass
        stop.wait(30)


def _publish(src_dir: Path, out_dir: Path, prefix: str, skip_top: set[str]) -> dict[str, int]:
    files: dict[str, int] = {}
    for f in src_dir.rglob("*"):
        if not f.is_file():
            continue
        rel = f.relative_to(src_dir)
        if rel.parts[0] in skip_top:
            continue
        key = f"{prefix}/{rel.as_posix()}" if prefix else rel.as_posix()
        target = out_dir / key
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(f, target)
        files[key] = f.stat().st_size
    return files


# ------------------------------------------------------------------ analyze
def task_analyze(job_dir: Path, spec: dict) -> tuple[dict[str, int], dict]:
    p = spec["params"]
    home = SCRATCH / job_dir.name
    shutil.rmtree(home, ignore_errors=True)
    shutil.copytree(job_dir / "in", home)
    (home / "models").symlink_to(MODELS, target_is_directory=True)
    os.environ["COMIC_HOME"] = str(home)
    os.environ["COMIC_ONNX_PROVIDER"] = "cuda"
    os.environ["COMIC_WORKERS"] = "1"

    from comic.paths import ChapterPaths
    from comic.stages import run_detect, run_ocr, run_slice, write_page_overlays
    from comic.state.store import ChapterStore

    paths = ChapterPaths(p["series"], p["chapter"])
    store = ChapterStore(paths)
    state = store.load()
    if p.get("language"):
        state.source_language = p["language"]
    pages = p.get("pages") or None
    report = run_slice(paths, state)
    if report is not None and report.spans:
        pages = None
    store.save(state)
    detected = run_detect(paths, state, only=pages, force=bool(p.get("force")))
    store.save(state)
    read, suspicious = run_ocr(paths, state, only=pages, force=bool(p.get("force")))
    store.save(state)
    write_page_overlays(paths, pages or [r.page for r in state.pages])

    prefix = f"workspace/{paths.series}/{paths.chapter}"
    files = _publish(paths.workspace, job_dir / "out", prefix, {"original"})
    summary = {"pages": detected.pages_done, "regions": read.regions,
               "suspicious": len(suspicious), "notes": detected.notes + read.notes}
    shutil.rmtree(home, ignore_errors=True)
    return files, summary


# ------------------------------------------------------------------ inpaint
_lama_model = None


def _device() -> str:
    import torch

    return "cuda:0" if torch.cuda.is_available() else "cpu"


def _lama():
    global _lama_model
    if _lama_model is None:
        import torch

        _lama_model = torch.jit.load(str(MODELS / LAMA_FILE), map_location=_device()).eval()
    return _lama_model


#: Batches of spots run as one GPU call; this many pixels per call at most
#: (the T4 has ~14 GB free -- 2026-11-13). Out-of-memory halves the batch.
BATCH_PIXELS = 24_000_000
#: Spots are padded up to a multiple of this to share a batch.
BUCKET = 128


def _prepare(image, mask):
    """The crop LaMa sees for one spot: mask bbox + margin, at native size up to
    LAMA_MAX_SIDE (the old 1536 cap shrank big holes and lost their detail)."""
    import numpy as np
    from PIL import Image

    sel = mask > 0
    if not sel.any():
        return None
    h, w = sel.shape
    ys, xs = np.where(sel)
    y1, y2, x1, x2 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    margin = max(48, int(0.5 * max(y2 - y1, x2 - x1)))
    y1, x1 = max(0, y1 - margin), max(0, x1 - margin)
    y2, x2 = min(h, y2 + margin), min(w, x2 + margin)
    crop, csel = image[y1:y2, x1:x2], sel[y1:y2, x1:x2]
    ch, cw = csel.shape
    scale = min(1.0, LAMA_MAX_SIDE / max(ch, cw))
    if scale < 1.0:
        size = (max(8, round(cw * scale)), max(8, round(ch * scale)))
        small = np.array(Image.fromarray(crop).resize(size, Image.LANCZOS))
        small_m = np.array(Image.fromarray(csel.astype(np.uint8) * 255).resize(size, Image.NEAREST)) > 0
    else:
        small, small_m = crop, csel
    return {"box": (y1, y2, x1, x2), "crop": crop, "sel": csel, "scale": scale,
            "small": small, "small_m": small_m}


def _finish(image, prep, out):
    import numpy as np
    from PIL import Image

    y1, y2, x1, x2 = prep["box"]
    ch, cw = prep["sel"].shape
    if prep["scale"] < 1.0:
        out = np.array(Image.fromarray(out).resize((cw, ch), Image.LANCZOS))
    patched = prep["crop"].copy()
    patched[prep["sel"]] = out[prep["sel"]]
    result = image.copy()
    result[y1:y2, x1:x2] = patched
    return result


def _run_batch(preps, H, W):
    """One GPU call for spots padded to H x W; returns each spot's painted crop."""
    import numpy as np
    import torch

    imgs, msks = [], []
    for p in preps:
        sh, sw = p["small_m"].shape
        # The padding is marked as hole, never as context: mirroring the crop
        # into known context copied the very letters being erased next to the
        # hole, and LaMa painted their ghost back in (Ku Ku ch.2 p.10 mw123.cc,
        # Kawaii ch.1 p.6, 2026-11-13).
        imgs.append(np.pad(p["small"], ((0, H - sh), (0, W - sw), (0, 0)), mode="edge"))
        msks.append(np.pad(p["small_m"].astype(np.float32), ((0, H - sh), (0, W - sw)),
                           mode="constant", constant_values=1.0))
    it = torch.from_numpy(np.stack(imgs)).permute(0, 3, 1, 2).to(_device(), torch.float32) / 255.0
    mt = torch.from_numpy(np.stack(msks)).unsqueeze(1).to(_device(), torch.float32)
    with torch.inference_mode():
        res = _lama()(it, mt)
    res = (res.permute(0, 2, 3, 1).clamp(0, 1).cpu().numpy() * 255).round().astype(np.uint8)
    del it, mt
    return [res[i, :p["small_m"].shape[0], :p["small_m"].shape[1]] for i, p in enumerate(preps)]


def _is_oom(exc) -> bool:
    return "out of memory" in str(exc).lower()


def _paint(preps, H, W):
    """Run a bucket, halving the batch on out-of-memory; a single spot that
    still does not fit is painted smaller (and resized back by _finish)."""
    import torch

    try:
        return _run_batch(preps, H, W)
    except (RuntimeError, torch.cuda.OutOfMemoryError) as exc:  # type: ignore[attr-defined]
        if not _is_oom(exc):
            raise
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        if len(preps) > 1:
            half = len(preps) // 2
            return _paint(preps[:half], H, W) + _paint(preps[half:], H, W)
        from PIL import Image
        import numpy as np

        p = dict(preps[0])
        sh, sw = p["small_m"].shape
        size = (max(8, int(sw * 0.75)), max(8, int(sh * 0.75)))
        p["small"] = np.array(Image.fromarray(p["small"]).resize(size, Image.LANCZOS))
        p["small_m"] = np.array(Image.fromarray(p["small_m"].astype(np.uint8) * 255).resize(size, Image.NEAREST)) > 0
        nh, nw = -(-size[1] // 8) * 8, -(-size[0] // 8) * 8
        out = _paint([p], nh, nw)[0]
        return [np.array(Image.fromarray(out).resize((sw, sh), Image.LANCZOS))]


def inpaint_batch(pairs):
    """LaMa for many (image, mask) spots at once: spots of similar size share
    one GPU call (the T4's VRAM sat idle with one small call per spot,
    2026-11-13). Returns new arrays in the same order."""
    preps = [_prepare(img, msk) for img, msk in pairs]
    results = [img.copy() for img, _ in pairs]
    buckets: dict[tuple[int, int], list[int]] = {}
    for i, p in enumerate(preps):
        if p is None:
            continue
        sh, sw = p["small_m"].shape
        key = (-(-sh // BUCKET) * BUCKET, -(-sw // BUCKET) * BUCKET)
        buckets.setdefault(key, []).append(i)
    for (H, W), idx in buckets.items():
        per = max(1, BATCH_PIXELS // (H * W))
        for k in range(0, len(idx), per):
            chunk = idx[k:k + per]
            outs = _paint([preps[i] for i in chunk], H, W)
            for i, out in zip(chunk, outs):
                results[i] = _finish(pairs[i][0], preps[i], out)
    return results


def inpaint_array(image, mask):
    """LaMa over the mask's bounding box plus a margin; returns a new array."""
    return inpaint_batch([(image, mask)])[0]


def task_inpaint(job_dir: Path, spec: dict) -> tuple[dict[str, int], dict]:
    import numpy as np
    from PIL import Image

    files: dict[str, int] = {}
    out_dir = job_dir / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    items = spec["params"]["items"]
    pairs = []
    for item in items:
        image = np.array(Image.open(job_dir / "in" / item["image"]).convert("RGB"))
        mask = np.array(Image.open(job_dir / "in" / item["mask"]).convert("L")) >= 128
        if mask.shape != image.shape[:2]:
            raise ValueError(f"{item['name']}: mask and image differ in size")
        pairs.append((image, mask))
    for item, result in zip(items, inpaint_batch(pairs)):
        target = out_dir / f"{item['name']}.png"
        Image.fromarray(result).save(target)
        files[target.name] = target.stat().st_size
    return files, {"items": len(files)}


TASKS = {"analyze": task_analyze, "inpaint": task_inpaint}


# ------------------------------------------------------------------ loop
def main(root: str | Path, poll_s: float = 5.0) -> None:
    root = Path(root)
    jobs = root / "jobs"
    jobs.mkdir(exist_ok=True)  # the PC creates the folder; never make it here
    SCRATCH.mkdir(parents=True, exist_ok=True)
    stop = threading.Event()
    threading.Thread(target=_beat, args=(root, stop), daemon=True).start()
    print("Worker ready -- leave this cell running. Stop it with the stop button.", flush=True)
    try:
        while True:
            for manifest in sorted(jobs.glob("*/job.json")):
                job_dir = manifest.parent
                if (job_dir / "done.json").exists() or (job_dir / "claimed").exists():
                    continue
                try:
                    spec = json.loads(manifest.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if not _arrived(job_dir, spec.get("files", {})):
                    continue
                (job_dir / "claimed").write_text(str(time.time()))
                _state["busy"] = job_dir.name
                started = time.time()
                print(f"[{time.strftime('%H:%M:%S')}] {job_dir.name} ...", flush=True)
                try:
                    task = TASKS[spec["task"]]
                    files, summary = task(job_dir, spec)
                    summary["env"] = environment()
                    done = {"ok": True, "files": files, "summary": summary}
                except Exception:  # report, never kill the worker
                    done = {"ok": False, "files": {}, "error": traceback.format_exc()}
                done["seconds"] = round(time.time() - started, 1)
                (job_dir / "done.json").write_text(json.dumps(done, ensure_ascii=False))
                _state["busy"] = None
                print(f"    {'ok' if done['ok'] else 'FAILED'} in {done['seconds']}s", flush=True)
                if not done["ok"]:
                    print(done["error"], flush=True)
            time.sleep(poll_s)
    finally:
        stop.set()
