"""Draw a ``VideoPlan`` into frames and hand them to FFmpeg.

Each shot is a virtual camera over one crop of a page's art: per frame the
camera's rectangle (zoom, pan, shake) is cut out of a pre-sharpened upscale
of the crop and resampled to 1080x1920. Static looks (grey, warm, glow) are
baked into the crop once; per-frame ones (grain, beat flips and flashes,
vignette) are cheap numpy passes. Transitions blend the outgoing shot -- its
camera simply keeps running past its end -- with the incoming one.

Frames go to FFmpeg as raw RGB on a pipe and are encoded with Intel Quick
Sync (``h264_qsv``) where it works, so the CPU only draws. Two files come out
of one encode: with the music, and with a silent track.
"""

from __future__ import annotations

import math
import subprocess
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from comic.paths import project_root
from comic.video.music import ffmpeg
from comic.video.schemas import (
    FPS, HEIGHT, WIDTH, Effect, Fit, Motion, Shot, TextAnim, TextPos, TextRole, Transition,
)
from comic.video.source import check_allowed

#: Text stays inside TikTok's UI-free area (strictest published figures).
SAFE_SIDE, SAFE_TOP, SAFE_BOTTOM = 120, 160, 480
#: A crop whose aspect is within this of 9:16 fills the frame under fit=auto.
COVER_ASPECT = (0.42, 0.78)


def ease(p: float) -> float:
    p = min(1.0, max(0.0, p))
    return p * p * (3 - 2 * p)


def ease_out(p: float) -> float:
    p = min(1.0, max(0.0, p))
    return 1 - (1 - p) ** 3


# -- the camera -------------------------------------------------------------

def camera(motion: Motion, strength: float, t: float, dur: float, seed: int) -> tuple[float, float, float]:
    """(zoom >= 1, dx, dy) at ``t`` seconds into a shot; dx/dy in -1..1 of the slack."""
    p = t / dur if dur else 1.0
    s = strength
    if motion is Motion.STATIC:
        return 1.0, 0.0, 0.0
    if motion is Motion.ZOOM_IN:
        return 1.0 + 0.22 * s * ease(p), 0.0, 0.0
    if motion is Motion.ZOOM_OUT:
        return 1.0 + 0.22 * s * (1 - ease(p)), 0.0, 0.0
    room = 1.0 + 0.12 + 0.18 * s
    if motion is Motion.PAN_LEFT:
        return room, -1 + 2 * ease(p), 0.0
    if motion is Motion.PAN_RIGHT:
        return room, 1 - 2 * ease(p), 0.0
    if motion is Motion.PAN_DOWN:
        return room, 0.0, -1 + 2 * ease(p)
    if motion is Motion.PAN_UP:
        return room, 0.0, 1 - 2 * ease(p)
    if motion is Motion.PUNCH:
        land = ease_out(t / 0.2)
        return 1.0 + (0.12 + 0.3 * s) * land + 0.03 * ease(p), 0.0, 0.0
    if motion is Motion.SHAKE:
        decay = math.exp(-t / 0.6) * (0.35 + 0.65 * s)
        rng = np.random.default_rng(seed + int(t * FPS))
        return 1.12, float(rng.uniform(-1, 1) * decay), float(rng.uniform(-1, 1) * decay)
    if motion is Motion.DRIFT:
        return (1.06 + 0.04 * s * math.sin(math.pi * p), 0.35 * s * math.sin(2 * math.pi * p * 0.5),
                0.2 * s * math.sin(2 * math.pi * p * 0.35 + 1.0))
    return 1.0, 0.0, 0.0


# -- looks ------------------------------------------------------------------

def static_looks(im: Image.Image, effects: set[Effect]) -> Image.Image:
    a = np.asarray(im).astype(np.float32)
    if Effect.GRAYSCALE in effects or Effect.DESATURATE in effects:
        g = a @ np.array([0.299, 0.587, 0.114], np.float32)
        mix = 1.0 if Effect.GRAYSCALE in effects else 0.65
        a = a * (1 - mix) + g[..., None] * mix
    # Tints stay subtle: on black-and-white manga a strong one reads as an
    # old yellowed scan (measured on the Super Lovers pilot, 2026-10-14).
    if Effect.WARM in effects:
        a *= np.array([1.025, 1.0, 0.96], np.float32)
    if Effect.COOL in effects:
        a *= np.array([0.96, 0.99, 1.04], np.float32)
    out = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))
    if Effect.GLOW in effects:
        blur = out.filter(ImageFilter.GaussianBlur(14))
        out = Image.blend(out, Image.fromarray(np.maximum(np.asarray(out), np.asarray(blur))), 0.3)
    return out


_VIGNETTE: np.ndarray | None = None


def vignette() -> np.ndarray:
    global _VIGNETTE
    if _VIGNETTE is None:
        y, x = np.ogrid[:HEIGHT, :WIDTH]
        r = np.sqrt(((x - WIDTH / 2) / (WIDTH / 2)) ** 2 + ((y - HEIGHT / 2) / (HEIGHT / 2)) ** 2)
        _VIGNETTE = np.clip(1.15 - 0.55 * r ** 2, 0.35, 1.0).astype(np.float32)[..., None]
    return _VIGNETTE


# -- text -------------------------------------------------------------------

def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    path = project_root() / "assets" / "fonts" / name
    font = ImageFont.truetype(str(path), size)
    try:  # variable fonts: take a heavy instance for legibility over art
        names = [n.decode() if isinstance(n, bytes) else n for n in font.get_variation_names()]
        for want in ("ExtraBold", "Bold", "SemiBold"):
            if want in names:
                font.set_variation_by_name(want)
                break
    except (OSError, AttributeError):
        pass
    return font


def wrap(text: str, font: ImageFont.FreeTypeFont, width: int) -> list[str] | None:
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if font.getlength(trial) <= width:
            cur = trial
        elif not cur:
            return None  # a single word wider than the line
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


ROLE_SIZE = {TextRole.HOOK: 84, TextRole.CAPTION: 62, TextRole.QUOTE: 60, TextRole.NOTE: 44}
ROLE_LINES = {TextRole.HOOK: 3, TextRole.CAPTION: 3, TextRole.QUOTE: 3, TextRole.NOTE: 4}


def text_layer(text: str, role: TextRole, font_name: str, reveal: int | None = None) -> Image.Image:
    """White lettering with a dark outline and soft shadow, transparent around.

    ``reveal`` draws only that many characters, in the full text's layout, so
    a typewriter reveal never re-wraps.
    """
    text = unicodedata.normalize("NFC", text)
    if role is TextRole.QUOTE and not text.startswith(("“", '"')):
        text = f"“{text}”"
    width = WIDTH - 2 * SAFE_SIDE
    size = ROLE_SIZE[role]
    while True:
        font = _font(font_name, size)
        lines = wrap(text, font, width)
        if lines and len(lines) <= ROLE_LINES[role] or size <= 34:
            break
        size -= 4
    lines = lines or [text]
    lead = round(size * 1.28)
    stroke = max(3, round(size * 0.09))
    pad = stroke + 18
    w = int(max(font.getlength(l) for l in lines)) + 2 * pad
    h = lead * len(lines) + 2 * pad
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    shadow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ds, d = ImageDraw.Draw(shadow), ImageDraw.Draw(layer)
    fill = {TextRole.HOOK: (255, 236, 120, 255), TextRole.NOTE: (255, 244, 190, 255)}.get(role, (255, 255, 255, 255))
    left = len(text) + 2 if reveal is None else reveal + (1 if role is TextRole.QUOTE else 0)
    for i, line in enumerate(lines):
        x = (w - font.getlength(line)) / 2
        y = pad + i * lead
        line = line[:max(0, left)]
        left -= len(line) + 1
        if not line:
            break
        ds.text((x + 4, y + 6), line, font=font, fill=(0, 0, 0, 150), stroke_width=stroke, stroke_fill=(0, 0, 0, 150))
        d.text((x, y), line, font=font, fill=fill, stroke_width=stroke, stroke_fill=(10, 10, 10, 255))
    shadow = shadow.filter(ImageFilter.GaussianBlur(6))
    return Image.alpha_composite(shadow, layer)


def text_anchor(layer: Image.Image, pos: TextPos) -> tuple[int, int]:
    x = (WIDTH - layer.width) // 2
    if pos is TextPos.TOP:
        y = SAFE_TOP + 60
    elif pos is TextPos.CENTER:
        y = (HEIGHT - layer.height) // 2
    else:
        y = HEIGHT - SAFE_BOTTOM - layer.height - 20
    return x, y


# -- shots ------------------------------------------------------------------

@dataclass
class Prepared:
    shot: Shot
    start: float
    seconds: float
    fit: Fit
    work: Image.Image                 # the crop, looks baked in, upscaled
    view: tuple[float, float]         # base camera size in work pixels (cover)
    bg: Image.Image | None = None     # contain: the blurred backdrop
    effects: set[Effect] = field(default_factory=set)
    text: Image.Image | None = None
    text_font: str = "BeVietnamPro-Bold.ttf"
    text_window: tuple[float, float] = (0.0, 0.0)
    text_cache: dict = field(default_factory=dict)
    seed: int = 0
    bg_kind: str = "blur"
    size: tuple[int, int] = (WIDTH, HEIGHT)
    #: Split screen: the image drawn in the bottom half (this one is the top).
    partner: "Prepared | None" = None


def prepare(shot: Shot, image_path: Path, box: tuple[int, int, int, int], start: float, seconds: float,
            plan_effects: list[Effect], text_font: str, index: int, background: str = "blur",
            size: tuple[int, int] = (WIDTH, HEIGHT)) -> Prepared:
    W, H = size
    src = Image.open(check_allowed(image_path)).convert("RGB")
    x1, y1, x2, y2 = box
    if shot.crop:
        x1, y1 = max(x1, shot.crop.x1), max(y1, shot.crop.y1)
        x2, y2 = min(x2, shot.crop.x2), min(y2, shot.crop.y2)
    crop = src.crop((x1, y1, x2, y2))
    effects = set(plan_effects) | set(shot.effects)
    crop = static_looks(crop, effects)
    aspect = crop.width / crop.height
    fit = shot.fit
    if fit is Fit.AUTO:
        target_aspect = W / H
        fit = Fit.COVER if COVER_ASPECT[0] <= aspect / target_aspect * (9 / 16) <= COVER_ASPECT[1] else Fit.CONTAIN

    if fit is Fit.COVER:
        target = W / H
        vw, vh = (crop.height * target, crop.height) if aspect > target else (crop.width, crop.width / target)
        k = max(1.0, H * 1.25 / vh)
        work = crop.resize((round(crop.width * k), round(crop.height * k)), Image.LANCZOS)
        work = work.filter(ImageFilter.UnsharpMask(radius=2, percent=60, threshold=2))
        prep = Prepared(shot, start, seconds, fit, work, (vw * k, vh * k), effects=effects, seed=index * 997,
                        size=size)
    else:
        fill = 1.0 if background != "blur" else 0.94
        k = min(W * fill / crop.width, H * (0.86 if background != "blur" else 0.7) / crop.height) * 1.25
        work = crop.resize((round(crop.width * k), round(crop.height * k)), Image.LANCZOS)
        work = work.filter(ImageFilter.UnsharpMask(radius=2, percent=60, threshold=2))
        # Backdrop: the same art filling the frame, blurred and darkened.
        target = W / H
        vw, vh = (crop.height * target, crop.height) if aspect > target else (crop.width, crop.width / target)
        cx, cy = crop.width / 2, crop.height / 2
        if background == "blur":
            bg = crop.crop((round(cx - vw / 2), round(cy - vh / 2), round(cx + vw / 2), round(cy + vh / 2)))
            bg = bg.resize((W // 4, H // 4), Image.BILINEAR).filter(ImageFilter.GaussianBlur(10))
            bg = Image.fromarray((np.asarray(bg).astype(np.float32) * 0.45).astype(np.uint8))
            bg = bg.resize((W, H), Image.BICUBIC)
        else:
            bg = Image.new("RGB", (W, H), (255, 255, 255) if background == "white" else (0, 0, 0))
        prep = Prepared(shot, start, seconds, fit, work, (0, 0), bg=bg, effects=effects, seed=index * 997,
                        bg_kind=background, size=size)

    if shot.text:
        prep.text_font = text_font
        prep.text = text_layer(shot.text.text, shot.text.role, text_font)
        end = shot.text.end if shot.text.end is not None else seconds
        prep.text_window = (min(shot.text.start, seconds - 0.3), min(end, seconds))
    return prep


def draw_shot(p: Prepared, t: float) -> Image.Image:
    """The shot's picture (no transition, no text) at ``t`` seconds into it."""
    if p.partner is not None:
        frame = Image.new("RGB", (WIDTH, HEIGHT))
        frame.paste(_draw_one(p, t), (0, 0))
        frame.paste(_draw_one(p.partner, t), (0, HEIGHT - p.partner.size[1]))
        return frame
    return _draw_one(p, t)


def _draw_one(p: Prepared, t: float) -> Image.Image:
    W, H = p.size
    zoom, dx, dy = camera(p.shot.motion, p.shot.strength, t, p.seconds, p.seed)
    if p.fit is Fit.COVER:
        W0, H0 = p.work.size
        vw, vh = p.view[0] / zoom, p.view[1] / zoom
        sx, sy = (W0 - vw) / 2, (H0 - vh) / 2          # slack each side
        cx, cy = W0 / 2 + dx * sx, H0 / 2 + dy * sy
        left = min(max(cx - vw / 2, 0), W0 - vw)
        top = min(max(cy - vh / 2, 0), H0 - vh)
        return p.work.transform((W, H), Image.EXTENT, (left, top, left + vw, top + vh), Image.BILINEAR)
    frame = p.bg.copy()
    fw, fh = p.work.size
    k = zoom / 1.25
    size = (max(1, round(fw * k)), max(1, round(fh * k)))
    fg = p.work.resize(size, Image.BILINEAR)
    x = (W - size[0]) // 2 + round(dx * 60)
    y = (H - size[1]) // 2 + round(dy * 60) - (40 if p.bg_kind == "blur" else 0)
    frame.paste(fg, (x, y))
    return frame


def beat_age(beats: list[float], t: float) -> tuple[int, float]:
    """(beats passed, seconds since the last) at video time ``t``."""
    n = int(np.searchsorted(beats, t, side="right")) if beats else 0
    return n, (t - beats[n - 1]) if n else 99.0


def finish(frame: Image.Image, p: Prepared, t_shot: float, t_video: float, beats: list[float]) -> Image.Image:
    """Per-frame looks, then the shot's text."""
    eff = p.effects
    n, age = beat_age(beats, t_video)
    if Effect.MIRROR_BEAT in eff and n % 2:
        frame = frame.transpose(Image.FLIP_LEFT_RIGHT)
    a = None
    if eff & {Effect.VIGNETTE, Effect.GRAIN, Effect.FLASH_BEAT, Effect.RGB_SPLIT}:
        a = np.asarray(frame).astype(np.float32)
        if Effect.RGB_SPLIT in eff and age < 0.25:
            k = int(10 * math.exp(-age / 0.08))
            if k:
                a[..., 0] = np.roll(a[..., 0], k, axis=1)
                a[..., 2] = np.roll(a[..., 2], -k, axis=1)
        if Effect.VIGNETTE in eff:
            a *= vignette()
        if Effect.GRAIN in eff:
            rng = np.random.default_rng(int(t_video * FPS))
            a += rng.normal(0, 7, (HEIGHT // 2, WIDTH // 2, 1)).repeat(2, 0).repeat(2, 1).astype(np.float32)
        if Effect.FLASH_BEAT in eff and age < 0.2:
            a += 140 * math.exp(-age / 0.05)
        frame = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))
    return overlay_text(frame, p, t_shot)


def overlay_text(frame: Image.Image, p: Prepared, t: float) -> Image.Image:
    if p.text is None:
        return frame
    s, e = p.text_window
    if not s <= t <= e:
        return frame
    anim = p.shot.text.anim
    local, left = t - s, e - t
    alpha = min(1.0, local / 0.25, left / 0.25) if anim is not TextAnim.POP else min(1.0, local / 0.08, left / 0.2)
    layer, scale, dy = p.text, 1.0, 0
    if anim is TextAnim.POP:
        q = min(1.0, local / 0.28)
        scale = 0.6 + 0.55 * ease_out(q) - 0.15 * max(0.0, q - 0.6) / 0.4
    elif anim is TextAnim.SLIDE_UP:
        dy = round(60 * (1 - ease_out(local / 0.4)))
    elif anim is TextAnim.TYPEWRITER:
        text = unicodedata.normalize("NFC", p.shot.text.text)
        n = max(1, min(len(text), int(len(text) * local / min(1.4, 0.6 * (e - s)))))
        if n < len(text):
            if n not in p.text_cache:
                p.text_cache[n] = text_layer(text, p.shot.text.role, p.text_font, reveal=n)
            layer = p.text_cache[n]
    if scale != 1.0:
        layer = layer.resize((max(1, round(layer.width * scale)), max(1, round(layer.height * scale))), Image.BILINEAR)
    if alpha < 1.0:
        a = np.asarray(layer).copy()
        a[..., 3] = (a[..., 3] * max(0.0, alpha)).astype(np.uint8)
        layer = Image.fromarray(a)
    x, y = text_anchor(p.text, p.shot.text.position)
    x += (p.text.width - layer.width) // 2
    y += (p.text.height - layer.height) // 2 + dy
    base = frame.convert("RGBA")
    base.alpha_composite(layer, (max(0, x), max(0, y)))
    return base.convert("RGB")


# -- transitions ------------------------------------------------------------

def blend(kind: Transition, a: Image.Image, b: Image.Image, q: float, seed: int) -> Image.Image:
    """The frame ``q`` (0..1) of the way through a transition from a to b."""
    q = min(1.0, max(0.0, q))
    if kind is Transition.CUT:
        return b
    if kind is Transition.FADE:
        return Image.blend(a, b, ease(q))
    if kind in (Transition.DIP_BLACK, Transition.DIP_WHITE):
        c = Image.new("RGB", a.size, "black" if kind is Transition.DIP_BLACK else "white")
        return Image.blend(a, c, ease(q * 2)) if q < 0.5 else Image.blend(c, b, ease(q * 2 - 1))
    if kind is Transition.FLASH:
        w = Image.new("RGB", b.size, "white")
        return Image.blend(b, w, (1 - q) ** 2)
    if kind in (Transition.SLIDE_UP, Transition.SLIDE_LEFT, Transition.WHIP):
        e = ease(q)
        out = a.copy()
        if kind is Transition.SLIDE_UP:
            off = round(HEIGHT * (1 - e))
            out.paste(b, (0, off))
            return out
        off = round(WIDTH * (1 - e))
        if kind is Transition.WHIP:
            k = max(1, int(40 * math.sin(math.pi * q)))
            a2 = a.filter(ImageFilter.BoxBlur(k))
            b2 = b.filter(ImageFilter.BoxBlur(k))
            out = Image.new("RGB", a.size)
            out.paste(a2, (off - WIDTH, 0))
            out.paste(b2, (off, 0))
            return out
        out.paste(b, (off, 0))
        return out
    if kind is Transition.ZOOM_BLUR:
        src, z = (a, 1 + 0.5 * q) if q < 0.5 else (b, 1 + 0.5 * (1 - q))
        w, h = round(WIDTH / z), round(HEIGHT / z)
        im = src.crop(((WIDTH - w) // 2, (HEIGHT - h) // 2, (WIDTH + w) // 2, (HEIGHT + h) // 2)).resize((WIDTH, HEIGHT))
        return im.filter(ImageFilter.GaussianBlur(12 * math.sin(math.pi * q)))
    if kind is Transition.GLITCH:
        src = a if q < 0.5 else b
        arr = np.asarray(src).copy()
        rng = np.random.default_rng(seed + int(q * 100))
        strength = math.sin(math.pi * q)
        for _ in range(int(14 * strength)):
            y0 = int(rng.integers(0, HEIGHT - 40))
            hgt = int(rng.integers(8, 90))
            arr[y0:y0 + hgt] = np.roll(arr[y0:y0 + hgt], int(rng.integers(-120, 120) * strength), axis=1)
        k = int(18 * strength)
        if k:
            arr[..., 0] = np.roll(arr[..., 0], k, axis=1)
            arr[..., 2] = np.roll(arr[..., 2], -k, axis=1)
        return Image.fromarray(arr)
    return b


# -- the timeline -----------------------------------------------------------

@dataclass
class Timeline:
    shots: list[Prepared]
    beats: list[float]

    @property
    def seconds(self) -> float:
        last = self.shots[-1]
        return last.start + last.seconds

    def frame(self, t: float) -> Image.Image:
        i = 0
        while i + 1 < len(self.shots) and t >= self.shots[i + 1].start:
            i += 1
        cur = self.shots[i]
        tl = t - cur.start
        b = finish(draw_shot(cur, tl), cur, tl, t, self.beats)
        if i == 0 or cur.shot.transition is Transition.CUT:
            return b
        td = min(cur.shot.transition_seconds, cur.seconds * 0.6)
        if tl >= td:
            return b
        prev = self.shots[i - 1]
        tp = t - prev.start
        a = finish(draw_shot(prev, tp), prev, tp, t, self.beats)
        return blend(cur.shot.transition, a, b, tl / td, i * 31)


def encode(timeline: Timeline, out: Path, on_frame=None) -> Path:
    """Render every frame through FFmpeg. Quick Sync first, x264 if it fails."""
    out.parent.mkdir(parents=True, exist_ok=True)
    total = int(round(timeline.seconds * FPS))
    encoders = [
        ["-vf", "format=nv12", "-c:v", "h264_qsv", "-global_quality", "21", "-look_ahead", "0"],
        ["-c:v", "libx264", "-preset", "medium", "-crf", "19", "-pix_fmt", "yuv420p", "-threads", "4"],
    ]
    last_error = ""
    for enc in encoders:
        cmd = [ffmpeg(), "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
               "-s", f"{WIDTH}x{HEIGHT}", "-r", str(FPS), "-i", "-", *enc,
               "-movflags", "+faststart", str(out)]
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            for n in range(total):
                t = n / FPS
                im = timeline.frame(t)
                if on_frame:
                    on_frame(n, t, im)
                proc.stdin.write(im.tobytes())
            proc.stdin.close()
            err = proc.stderr.read().decode(errors="replace")
            if proc.wait() == 0 and out.exists():
                return out
            last_error = err
        except BrokenPipeError:
            last_error = proc.stderr.read().decode(errors="replace")
            proc.wait()
        on_frame = None  # the second attempt need not repeat side effects
    raise RuntimeError(f"ffmpeg could not encode: {last_error.strip()[-400:]}")


def mux(video: Path, audio: Path | None, out: Path, seconds: float) -> Path:
    """Add the music, or a silent stereo track when ``audio`` is None."""
    if audio is not None:
        inputs = ["-i", str(audio)]
    else:
        inputs = ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"]
    subprocess.run([ffmpeg(), "-v", "error", "-y", "-i", str(video), *inputs, "-map", "0:v", "-map", "1:a",
                    "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-t", f"{seconds:.3f}",
                    "-movflags", "+faststart", str(out)], check=True)
    return out
