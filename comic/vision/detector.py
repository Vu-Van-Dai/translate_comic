"""Text detection via comic-text-detector (ONNX).

One forward pass yields everything the next three stages need:

``blk``  YOLO-style text-block proposals -> the regions we OCR and translate
``seg``  a pixel mask of the text itself  -> what the cleaning stage erases
``det``  line/border maps                 -> refines the mask near bubble edges

Using one model for both detection and masking matters: a box-only detector
would force the cleaner to guess which pixels are lettering, and erasing a whole
box destroys artwork inside the bubble.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np

from comic.paths import models_dir
from comic.schemas import BBox

log = logging.getLogger(__name__)

MODEL_NAME = "comictextdetector.onnx"
INPUT_SIZE = 1024

#: Minimum objectness before a proposal is considered at all.
DEFAULT_CONF = 0.40
#: IoU above which two proposals are treated as the same block.
DEFAULT_IOU = 0.35
#: Probability above which a pixel counts as text in the segmentation mask.
DEFAULT_MASK_THRESHOLD = 0.30


class DetectorError(RuntimeError):
    """Raised when the detector model is missing or unusable."""


@dataclass(frozen=True)
class Detection:
    bbox: BBox
    confidence: float
    #: The model's own class index; 0 and 1 distinguish bubble vs free text.
    cls: int


@dataclass
class DetectionResult:
    detections: list[Detection]
    #: uint8 text mask at the original page resolution; 255 = text pixel.
    mask: np.ndarray
    width: int
    height: int

    @property
    def count(self) -> int:
        return len(self.detections)


def model_path() -> Path:
    return models_dir() / MODEL_NAME


#: Execution providers to try, most preferred first.
#:
#: Prefers DirectML, falls back to CPU when that build is absent -- which is
#: safe, because a provider that is not installed is simply dropped from the
#: list rather than raising.
#:
#: Measured on Intel Iris Xe integrated graphics: whole-page text detection
#: went from 30.11s to 0.79s a page, a 38x speedup, with every derived region
#: identical (boxes, kinds, reading order, polygons; the segmentation mask
#: differed by 1-2 pixels in 13 million, which nothing downstream can see).
#:
#: This applies only to models that run one large inference per page. It is
#: emphatically wrong for RapidOCR, which is fed small crops: the same GPU
#: made a single crop 51x *slower*. See ``RapidOcrProvider.__init__``.
#:
#:     set COMIC_ONNX_PROVIDER=dml     -> DmlExecutionProvider, then CPU
#:     set COMIC_ONNX_PROVIDER=cpu     -> CPU only (the default)
#:
#: CPU is always appended as the last resort, so an unsupported operator falls
#: back per-node instead of failing the whole session.
PROVIDER_ALIASES = {
    "dml": "DmlExecutionProvider",
    "directml": "DmlExecutionProvider",
    "cuda": "CUDAExecutionProvider",
    "openvino": "OpenVINOExecutionProvider",
    "cpu": "CPUExecutionProvider",
}


def onnx_providers() -> list[str]:
    """The provider list for a session, honouring COMIC_ONNX_PROVIDER."""
    import os

    import onnxruntime as ort

    available = set(ort.get_available_providers())
    wanted = os.environ.get("COMIC_ONNX_PROVIDER", "dml").strip().lower()
    chosen: list[str] = []
    for token in (t for t in wanted.replace(",", " ").split() if t):
        name = PROVIDER_ALIASES.get(token, token)
        if name in available and name not in chosen:
            chosen.append(name)
    if "CPUExecutionProvider" not in chosen:
        chosen.append("CPUExecutionProvider")
    return chosen


@lru_cache(maxsize=1)
def _session():
    """Load the ONNX session once; it is ~90 MB and slow to initialise."""
    path = model_path()
    if not path.exists():
        raise DetectorError(
            f"missing {path}. Run: python scripts/fetch_models.py"
        )
    try:
        import onnxruntime as ort
    except ImportError as exc:  # pragma: no cover - covered by doctor
        raise DetectorError("onnxruntime is not installed") from exc

    from comic.cpu import ort_options

    options = ort_options()
    options.log_severity_level = 3
    return ort.InferenceSession(str(path), options, providers=onnx_providers())


def letterbox(image: np.ndarray, size: int = INPUT_SIZE) -> tuple[np.ndarray, float, int, int]:
    """Resize preserving aspect ratio, padding to a square.

    Returns the padded image plus the scale and offsets needed to map
    coordinates back. Getting this mapping wrong silently shifts every box, so
    it is returned explicitly rather than recomputed by callers.
    """
    import cv2

    h, w = image.shape[:2]
    scale = min(size / w, size / h)
    new_w, new_h = int(round(w * scale)), int(round(h * scale))
    resized = cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_LINEAR)

    canvas = np.full((size, size, 3), 114, dtype=np.uint8)
    pad_x, pad_y = (size - new_w) // 2, (size - new_h) // 2
    canvas[pad_y:pad_y + new_h, pad_x:pad_x + new_w] = resized
    return canvas, scale, pad_x, pad_y


def _nms(boxes: np.ndarray, scores: np.ndarray, threshold: float) -> list[int]:
    """Greedy non-maximum suppression over xyxy boxes."""
    if len(boxes) == 0:
        return []
    x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
    areas = (x2 - x1) * (y2 - y1)
    order = scores.argsort()[::-1]

    keep = []
    while order.size > 0:
        i = order[0]
        keep.append(int(i))
        if order.size == 1:
            break
        xx1 = np.maximum(x1[i], x1[order[1:]])
        yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]])
        yy2 = np.minimum(y2[i], y2[order[1:]])
        inter = np.clip(xx2 - xx1, 0, None) * np.clip(yy2 - yy1, 0, None)
        iou = inter / (areas[i] + areas[order[1:]] - inter + 1e-9)
        order = order[1:][iou <= threshold]
    return keep


def detect(
    image: np.ndarray,
    conf_threshold: float = DEFAULT_CONF,
    iou_threshold: float = DEFAULT_IOU,
    mask_threshold: float = DEFAULT_MASK_THRESHOLD,
) -> DetectionResult:
    """Run detection on an RGB page.

    ``image`` is HxWx3 uint8 RGB at the page's own resolution.
    """
    import cv2

    if image.ndim != 3 or image.shape[2] != 3:
        raise DetectorError(f"expected an HxWx3 RGB image, got {image.shape}")

    height, width = image.shape[:2]
    padded, scale, pad_x, pad_y = letterbox(image)

    tensor = padded.astype(np.float32) / 255.0
    tensor = np.transpose(tensor, (2, 0, 1))[None]  # NCHW

    session = _session()
    blk, seg, _det = session.run(None, {session.get_inputs()[0].name: tensor})

    # --- boxes -----------------------------------------------------------
    proposals = blk[0]                      # (N, 7): cx, cy, w, h, obj, cls...
    objectness = proposals[:, 4]
    keep = objectness >= conf_threshold
    proposals = proposals[keep]

    detections: list[Detection] = []
    if len(proposals):
        cx, cy, bw, bh = proposals[:, 0], proposals[:, 1], proposals[:, 2], proposals[:, 3]
        xyxy = np.stack([cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2], axis=1)
        scores = proposals[:, 4]
        classes = proposals[:, 5:].argmax(axis=1) if proposals.shape[1] > 5 else np.zeros(len(proposals), int)

        for index in _nms(xyxy, scores, iou_threshold):
            x1, y1, x2, y2 = xyxy[index]
            # Undo the letterbox: remove padding, then undo the scale.
            x1 = (x1 - pad_x) / scale
            x2 = (x2 - pad_x) / scale
            y1 = (y1 - pad_y) / scale
            y2 = (y2 - pad_y) / scale
            x1, y1 = max(0, int(round(x1))), max(0, int(round(y1)))
            x2, y2 = min(width, int(round(x2))), min(height, int(round(y2)))
            if x2 - x1 < 4 or y2 - y1 < 4:
                continue
            detections.append(
                Detection(
                    bbox=BBox(x1=x1, y1=y1, x2=x2, y2=y2),
                    confidence=float(scores[index]),
                    cls=int(classes[index]),
                )
            )

    # --- mask ------------------------------------------------------------
    raw_mask = seg[0, 0]
    if raw_mask.max() > 1.0 or raw_mask.min() < 0.0:
        raw_mask = 1.0 / (1.0 + np.exp(-raw_mask))   # logits -> probability

    # Crop the padding away before resizing, so the mask lines up with the page.
    inner_w, inner_h = int(round(width * scale)), int(round(height * scale))
    cropped = raw_mask[pad_y:pad_y + inner_h, pad_x:pad_x + inner_w]
    mask = cv2.resize(cropped, (width, height), interpolation=cv2.INTER_LINEAR)
    mask = (mask >= mask_threshold).astype(np.uint8) * 255

    return DetectionResult(
        detections=sorted(detections, key=lambda d: (d.bbox.y1, d.bbox.x1)),
        mask=mask,
        width=width,
        height=height,
    )


def load_page(path: Path) -> np.ndarray:
    """Read a page file as RGB, whatever its stored format."""
    import cv2

    data = np.fromfile(str(path), dtype=np.uint8)   # handles non-ASCII paths
    image = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if image is None:
        raise DetectorError(f"could not decode {path}")
    return cv2.cvtColor(image, cv2.COLOR_BGR2RGB)


def is_grayscale(image: np.ndarray, tolerance: int = 12) -> bool:
    """Whether a page is effectively black and white.

    Drives screentone-safe cleaning: a grayscale page must not be denoised or
    re-encoded in ways that destroy halftone dots.
    """
    sample = image[::4, ::4].astype(np.int16)
    spread = sample.max(axis=2) - sample.min(axis=2)
    return bool(spread.mean() < tolerance)
