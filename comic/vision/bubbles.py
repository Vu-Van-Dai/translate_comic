"""Speech-bubble shapes from a local segmentation model.

The flood fill in ``image_processing.mask.bubble_interior`` finds a bubble
from its lettering outward and is pixel-exact when the outline is closed. Two
shapes defeat it (Kimi ga Tomoshite ch.1, the operator 2026-10-28):

- a **dashed or dotted** outline (thought bubbles): the fill escapes through
  the gaps -- p.8 r07 measured 280x363 px for a ~130 px bubble -- or the leak
  guard rejects it and the line is squeezed into its text box (r04, r05);
- a bubble **cut by the panel edge**: p.17 r05's area ran past the border.

A YOLOv8 segmentation model trained on speech bubbles
(``models/bubble_seg_yolov8m.onnx``, kitsumed/yolov8m_seg-speech-bubble,
AGPL-3.0, run with onnxruntime -- no network, no pickle) outlines both. It is
a narrow tool like the others: it only says where bubbles are. Its edges are
approximate, so ``fit_areas`` uses a bubble only as an outer limit on a fill
that leaked and as the interior when the fill found none.

No model file, no bubbles: the pipeline behaves exactly as before.
"""
from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path

import numpy as np

MODEL_NAME = "bubble_seg_yolov8m.onnx"
INPUT_SIZE = 1024          # multiple of 32; the long side of the page is scaled to it
CONFIDENCE = 0.3
NMS_IOU = 0.5
THREADS = 2                # render runs pages in parallel; keep each one light (cpu-limit)


def model_path() -> Path:
    from comic.paths import project_root

    return project_root() / "models" / MODEL_NAME


@lru_cache(maxsize=1)
def _session(path: str):
    import onnxruntime as ort

    options = ort.SessionOptions()
    options.intra_op_num_threads = THREADS
    # On the Colab T4 the worker sets COMIC_ONNX_PROVIDER=cuda (as for detection).
    import os

    want = os.environ.get("COMIC_ONNX_PROVIDER", "cpu").strip().lower()
    providers = (["CUDAExecutionProvider", "CPUExecutionProvider"] if want == "cuda"
                 else ["DmlExecutionProvider", "CPUExecutionProvider"] if want == "dml"
                 else ["CPUExecutionProvider"])
    available = set(ort.get_available_providers())
    return ort.InferenceSession(path, options, providers=[p for p in providers if p in available])


def _detect(image: np.ndarray, path: Path) -> list[np.ndarray]:
    import cv2

    h, w = image.shape[:2]
    scale = INPUT_SIZE / max(h, w)
    nh, nw = int(round(h * scale)), int(round(w * scale))
    padded = np.full((INPUT_SIZE, INPUT_SIZE, 3), 114, np.uint8)
    padded[:nh, :nw] = cv2.resize(image[:, :, :3], (nw, nh))
    batch = padded.transpose(2, 0, 1)[None].astype(np.float32) / 255.0   # RGB, as trained

    out, protos = _session(str(path)).run(None, {"images": batch})
    pred = out[0].T                                  # anchors x (4 box + 1 score + 32 coef)
    keep = pred[:, 4] > CONFIDENCE
    pred = pred[keep]
    if not len(pred):
        return []
    boxes = np.stack([pred[:, 0] - pred[:, 2] / 2, pred[:, 1] - pred[:, 3] / 2,
                      pred[:, 2], pred[:, 3]], 1)
    chosen = np.array(cv2.dnn.NMSBoxes(boxes.tolist(), pred[:, 4].tolist(),
                                       CONFIDENCE, NMS_IOU)).reshape(-1)
    proto = protos[0]
    mh, mw = proto.shape[1:]
    masks = []
    for i in chosen:
        logits = (pred[i, 5:] @ proto.reshape(proto.shape[0], -1)).reshape(mh, mw)
        prob = cv2.resize(1.0 / (1.0 + np.exp(-logits)), (INPUT_SIZE, INPUT_SIZE))[:nh, :nw]
        mask = cv2.resize(prob, (w, h)) > 0.5
        x, y, bw, bh = boxes[i] / scale
        inside = np.zeros_like(mask)
        inside[max(0, int(y)): int(y + bh) + 1, max(0, int(x)): int(x + bw) + 1] = True
        mask &= inside
        if mask.any():
            masks.append(mask)
    return masks


_CACHE: dict[str, list[np.ndarray]] = {}


def page_key(image: np.ndarray) -> str:
    """The cache key of a page: its pixels, as the pipeline loaded them."""
    return hashlib.sha1(np.ascontiguousarray(image).tobytes()).hexdigest()


def disk_cache() -> Path:
    """Masks computed once -- here or on the Colab T4 -- and reused by every stage
    and every later run (the model costs ~8 s a page on this PC's CPU,
    2026-11-13; the T4 fills this folder for a whole chapter in one job)."""
    from comic.paths import project_root

    return project_root() / "workspace" / "_cache" / "bubbles"


def save_masks(path: Path, masks: list[np.ndarray], shape) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    stack = np.stack(masks).astype(bool) if masks else np.zeros((0, *shape[:2]), bool)
    tmp = path.with_suffix(".tmp.npz")
    np.savez_compressed(tmp, packed=np.packbits(stack, axis=-1), shape=np.array(stack.shape))
    tmp.replace(path)


def load_masks(path: Path) -> list[np.ndarray] | None:
    try:
        data = np.load(path)
        n, h, w = (int(v) for v in data["shape"])
        stack = np.unpackbits(data["packed"], axis=-1, count=w).astype(bool).reshape(n, h, w)
        return list(stack)
    except (OSError, KeyError, ValueError):
        return None


def bubble_masks(image: np.ndarray) -> list[np.ndarray]:
    """Boolean masks of every speech bubble on the page ([] without the model).

    Cached per image content, so the renderer, the Photoshop plan and the
    placement checks -- which each call ``fit_areas`` -- see the same bubbles
    and run the model once per page.
    """
    path = model_path()
    if not path.exists():
        return []
    key = page_key(image)
    if key not in _CACHE:
        if len(_CACHE) > 8:
            _CACHE.clear()
        on_disk = disk_cache() / f"{key}.npz"
        masks = load_masks(on_disk) if on_disk.exists() else None
        if masks is None:
            masks = _detect(image, path)
            try:
                save_masks(on_disk, masks, image.shape)
            except OSError:
                pass
        _CACHE[key] = masks
    return _CACHE[key]
