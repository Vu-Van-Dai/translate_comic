"""Videos from any folder of images -- the operator's channel folders.

    comic video folder "D:/Kênh/Kênh tiktok/bl/manhwa/omega complex" [--recipe split] [--track tt_...]

The operator keeps finished images per series in channel folders
(``D:/Kênh/Kênh tiktok/bl/<manga|manhua|manhwa|lụm nhặt>/<series>/``) and
wants one video per folder, saved beside the images (2026-10-14). Those
images are the operator's own material, outside the pipeline, so the
source rule of ``video/source.py`` (never *our* translation) does not apply.

Variety is the point -- "I want to avoid repetitive formats": each folder
gets a *recipe* (an editing style, below) and a track, and ``batch`` spreads
both so neighbouring videos never share either. Recipes reuse the renderer's
closed vocabulary (motions, transitions, effects, split screen); the length
follows the number of images (15-35 s), since a 3-image folder held for
40 s drags.
"""

from __future__ import annotations

import json
import random
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from comic.video import music as music_lib
from comic.video.schemas import HEIGHT, WIDTH, Crop, Effect, Fit, Motion, Shot, Transition

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".jfif", ".bmp", ".gif"}
VIDEO_EXT = {".mp4", ".mov", ".webm", ".mkv"}


@dataclass
class Recipe:
    name: str
    about: str
    beats: int                       # beats per shot
    fit: Fit
    motions: list[Motion]
    transitions: list[Transition]
    tsec: float                      # transition length
    effects: list[Effect] = field(default_factory=list)
    background: str = "white"
    strength: float = 0.15
    split: bool = False              # two images stacked per shot
    moods: list[str] = field(default_factory=list)
    reuse: bool = False              # re-cut images (other crops) to fill the length
    captioned: bool = False          # needs a caption per image (written by the agent)


M, T, E, F = Motion, Transition, Effect, Fit
RECIPES: dict[str, Recipe] = {r.name: r for r in [
    Recipe("recap", "whole images on white, a hard cut every 4 beats, barely moving", 4, F.CONTAIN,
           [M.ZOOM_IN, M.STATIC], [T.CUT], 0.2, moods=["pop", "vpop", "upbeat", "emotional", "romantic"]),
    Recipe("beat", "full-frame re-cuts every 2 beats, punch and shake, glitch switches", 2, F.COVER,
           [M.PUNCH, M.SHAKE, M.ZOOM_IN, M.DRIFT], [T.GLITCH, T.CUT, T.FLASH], 0.15, strength=0.6,
           moods=["kpop", "energetic", "phonk", "jpop"], reuse=True),
    Recipe("split", "two images stacked, long holds, soft dips to white", 8, F.COVER,
           [M.ZOOM_IN, M.DRIFT], [T.DIP_WHITE, T.FADE], 0.5, effects=[E.GLOW], split=True,
           moods=["vietnamese", "vpop", "romantic", "chill", "trending"]),
    Recipe("pan", "each image travels top to bottom, crossfades", 6, F.COVER,
           [M.PAN_DOWN], [T.FADE], 0.6, strength=0.9, moods=["chill", "soft", "emotional", "jpop"]),
    Recipe("dreamy", "floating over a blurred copy, glow and slow fades", 5, F.CONTAIN,
           [M.ZOOM_IN, M.DRIFT, M.ZOOM_OUT], [T.FADE], 0.8, effects=[E.GLOW], background="blur",
           strength=0.3, moods=["romantic", "soft", "pop", "chill"]),
    Recipe("night", "on black, vignette, dips through black", 5, F.CONTAIN,
           [M.ZOOM_OUT, M.DRIFT, M.ZOOM_IN], [T.DIP_BLACK], 0.7, effects=[E.VIGNETTE], background="black",
           strength=0.3, moods=["melancholic", "emotional", "sad", "chill"]),
    Recipe("flash", "full frame, white flash on every cut and a light glow", 4, F.COVER,
           [M.ZOOM_IN, M.PUNCH], [T.FLASH], 0.3, effects=[E.GLOW], strength=0.25,
           moods=["kpop", "pop", "upbeat", "cpop"]),
    Recipe("zoomblur", "push in and pull out, zoom-blur switches", 4, F.COVER,
           [M.ZOOM_IN, M.ZOOM_OUT], [T.ZOOM_BLUR], 0.35, strength=0.4,
           moods=["energetic", "jpop", "anime", "kpop", "phonk"]),
    Recipe("whip", "quick whips and shakes, comedy timing", 3, F.CONTAIN,
           [M.PUNCH, M.SHAKE, M.STATIC], [T.WHIP], 0.25, strength=0.5,
           moods=["upbeat", "comedy", "trending", "vietnamese", "kpop"], reuse=True),
    Recipe("slide", "images slide in over white, gentle drift", 4, F.CONTAIN,
           [M.DRIFT, M.ZOOM_IN], [T.SLIDE_UP, T.SLIDE_LEFT], 0.4, strength=0.25,
           moods=["pop", "cpop", "us", "vpop", "chill"]),
    # Measured on the operator's reference page (Lazy Kids Team reels, 2026-10-15,
    # 8-62K views): full-frame crops, one joking narration line per shot in white
    # bold with a dark outline, 2-4 s holds, zoom-blur / glitch / black-dip
    # switches over a trending remix. The captions are the agent's, per image.
    Recipe("caption", "full frame, a joking narration line on every shot, blur and glitch switches", 4, F.COVER,
           [M.ZOOM_IN, M.DRIFT, M.ZOOM_OUT, M.PUNCH], [T.ZOOM_BLUR, T.GLITCH, T.DIP_BLACK, T.FLASH], 0.3,
           strength=0.3, moods=["trending", "remix", "vpop", "cpop", "chinese", "halloween"], captioned=True),
]}

#: Reading time for a caption: seconds = clamp(base + chars / rate).
CAPTION_BASE, CAPTION_RATE, CAPTION_MIN, CAPTION_MAX = 1.3, 15.0, 2.4, 5.0


#: Whole-desktop captures (a browser, the taskbar) kept as notes -- not art.
#: Cropped "Screenshot_N.png" files *are* the art in many folders, so the
#: test is the size of a whole screen, not the name.
DESKTOP_SIZES = {(1920, 1080), (1366, 768), (1536, 864), (1600, 900), (2560, 1440), (1440, 900), (1280, 800)}


def images(folder: Path) -> list[Path]:
    out = []
    for f in sorted(folder.iterdir()):
        if not f.is_file() or f.suffix.lower() not in IMAGE_EXT:
            continue
        try:
            with Image.open(f) as im:
                if im.size in DESKTOP_SIZES:
                    continue
        except OSError:
            continue
        out.append(f)
    return out


def eligible(folder: Path) -> bool:
    """A leaf folder with images and no video yet (the operator's rule)."""
    if not folder.is_dir():
        return False
    entries = list(folder.iterdir())
    if any(e.is_dir() for e in entries):
        return False
    if any(e.suffix.lower() in VIDEO_EXT for e in entries):
        return False
    return bool(images(folder))


def target_seconds(n_images: int) -> float:
    return max(15.0, min(35.0, 3.2 * n_images))


def _crops(path: Path, recipe: Recipe) -> list[Crop | None]:
    """Framings of one image: the whole, then (for re-cut recipes) its halves."""
    with Image.open(path) as im:
        w, h = im.size
    out: list[Crop | None] = [None]
    if recipe.reuse or recipe.split:
        if h > w * 1.25:                       # tall: top and bottom halves
            out += [Crop(x1=0, y1=0, x2=w, y2=int(h * 0.55)), Crop(x1=0, y1=int(h * 0.45), x2=w, y2=h)]
        elif w > h * 1.25:                     # wide: left and right
            out += [Crop(x1=0, y1=0, x2=int(w * 0.55), y2=h), Crop(x1=int(w * 0.45), y1=0, x2=w, y2=h)]
        else:
            out += [Crop(x1=int(w * 0.15), y1=int(h * 0.1), x2=int(w * 0.85), y2=int(h * 0.8))]
    return out


def frame_crop(path: Path, crop: Crop | None, aspect: float) -> Crop | None:
    """For a full-frame (cover) shot: the ``aspect`` (w/h) window of ``crop``
    (or the whole image), centred across but anchored near the top -- faces
    sit in the upper part of a figure, and a centred window on a tall image
    lands on legs (measured on the first beat test)."""
    with Image.open(path) as im:
        W, H = im.size
    x1, y1, x2, y2 = (crop.x1, crop.y1, crop.x2, crop.y2) if crop else (0, 0, W, H)
    w, h = x2 - x1, y2 - y1
    if w / h >= aspect:                        # wide enough: centre across
        nw = int(h * aspect)
        x1 += (w - nw) // 2
        return Crop(x1=x1, y1=y1, x2=x1 + nw, y2=y2)
    nh = int(w / aspect)
    top = y1 + int((h - nh) * 0.18)
    return Crop(x1=x1, y1=top, x2=x2, y2=top + nh)


def best_start(track, seconds: float) -> float:
    """Start on a beat inside the loudest stretch of ``seconds``."""
    a = music_lib.analyse(track)
    energy, beats, dur = a.get("energy") or [], a.get("beats") or [0.0], a["duration"]
    span = int(round(seconds))
    best, best_t = -1.0, beats[0]
    for b in beats:
        if b + seconds > dur - 0.5:
            break
        window = energy[int(b): int(b) + span]
        if len(window) < span * 0.9:
            continue
        score = sum(window) / len(window)
        if score > best + 1e-6:
            best, best_t = score, b
    return round(best_t, 3)


def plan(folder: Path, recipe: Recipe, track, seed: int, only: list[Path] | None = None,
         captions: dict[str, tuple[str, str]] | None = None,
         ) -> tuple[list[tuple[Shot, Path, Path | None]], float, float]:
    """Shots for ``folder``: (shot, image, partner image), the music start and length.

    ``only`` picks and orders the images (two videos from one folder);
    ``captions`` maps an image's file name to (text, position) for the
    ``caption`` recipe -- every captioned image is shown once, held for as
    long as its line takes to read."""
    rng = random.Random(seed)
    imgs = list(only) if only else images(folder)
    if recipe.captioned:
        from comic.video.schemas import TextOverlay, TextPos
        shots = []
        for i, p in enumerate(imgs):
            text, pos = (captions or {}).get(p.name, ("", "center"))
            secs = min(CAPTION_MAX, max(CAPTION_MIN, CAPTION_BASE + len(text) / CAPTION_RATE)) if text else 2.6
            kw = dict(panel="p000_k0", seconds=round(secs, 3), motion=recipe.motions[i % len(recipe.motions)],
                      strength=recipe.strength,
                      transition=recipe.transitions[(i + seed) % len(recipe.transitions)] if i else T.CUT,
                      transition_seconds=recipe.tsec, fit=recipe.fit, effects=list(recipe.effects),
                      crop=frame_crop(p, None, WIDTH / HEIGHT))
            if text:
                kw["text"] = TextOverlay(text=text, position=TextPos(pos), start=0.15)
            shots.append((Shot(**kw), p, None))
        seconds = sum(s.seconds for s, _, _ in shots)
        return shots, best_start(track, seconds + 1.0), seconds
    bpm = music_lib.analyse(track)["bpm"] or 100.0   # ambient tracks have no detectable beat
    beat = 60.0 / bpm
    total = target_seconds(len(imgs))
    base = recipe.beats * beat
    while base > 8.0:                          # Shot.seconds is capped at 8
        base /= 2
    if recipe.split:
        pairs = [(imgs[i], imgs[(i + 1) % len(imgs)]) for i in range(0, len(imgs), 2)]
        n = max(2, min(len(pairs), round(total / base)))
        units = [pairs[round(k * (len(pairs) - 1) / max(1, n - 1))] if len(pairs) >= n else pairs[k % len(pairs)]
                 for k in range(n)]
    elif recipe.reuse:
        whole = [(p, None) for p in imgs]
        parts = [(p, c) for p in imgs for c in _crops(p, recipe) if c is not None]
        rng.shuffle(parts)
        frames = whole + parts                 # every image once, then details
        n = max(4, round(total / base))
        units = [frames[k % len(frames)] for k in range(n)]
    else:
        # Each image at most once, spread across the folder; few images hold longer
        # (a 3-image folder looped to 15 s showed every image three times).
        n = max(1, min(len(imgs), round(total / base)))
        units = [(imgs[round(k * (len(imgs) - 1) / max(1, n - 1))], None) for k in range(n)]
    per = min(8.0, max(base, total / len(units)))
    shots = []
    for i, u in enumerate(units):
        kw = dict(panel="p000_k0", seconds=round(per, 3), motion=recipe.motions[i % len(recipe.motions)],
                  strength=recipe.strength, transition=recipe.transitions[i % len(recipe.transitions)] if i else T.CUT,
                  transition_seconds=recipe.tsec, fit=recipe.fit, effects=list(recipe.effects))
        if recipe.split:
            a, b = u
            shots.append((Shot(**kw, crop=frame_crop(a, None, WIDTH / (HEIGHT / 2)),
                               crop2=frame_crop(b, None, WIDTH / (HEIGHT / 2)), panel2="p000_k0",
                               motion2=recipe.motions[(i + 1) % len(recipe.motions)]), a, b))
        else:
            p, crop = u
            if recipe.fit is F.COVER and recipe.motions != [M.PAN_DOWN]:
                crop = frame_crop(p, crop, WIDTH / HEIGHT)
            if crop is not None:
                kw["crop"] = crop
            shots.append((Shot(**kw), p, None))
    seconds = sum(s.seconds for s, _, _ in shots)
    return shots, best_start(track, seconds + 1.0), seconds


def safe_name(text: str) -> str:
    return re.sub(r'[\\/:*?"<>|\t\r\n]+', " ", text).strip()[:80] or "track"


def render_folder(folder: Path, recipe_name: str, track_id: str, seed: int = 0, log=print,
                  only: list[Path] | None = None, captions: dict[str, tuple[str, str]] | None = None,
                  out_name: str | None = None) -> Path:
    from comic.video.render import Timeline, encode, mux, prepare

    recipe = RECIPES[recipe_name]
    track = music_lib.library()[track_id]
    shots, start, _ = plan(folder, recipe, track, seed, only, captions)
    info = music_lib.analyse(track)
    durations = music_lib.snap_cuts([s.seconds for s, _, _ in shots], info["beats"], start)
    beats_video = [round(b - start, 3) for b in info["beats"] if start <= b <= start + sum(durations) + 1]
    prepared, t = [], 0.0
    for i, ((shot, img, img2), secs) in enumerate(zip(shots, durations)):
        shot = shot.model_copy(update={"seconds": secs})
        with Image.open(img) as im:
            box = (0, 0, *im.size)
        if img2 is not None:
            half = (WIDTH, HEIGHT // 2)
            top = prepare(shot, img, box, t, secs, [], "BeVietnamPro-Bold.ttf", i + seed, recipe.background, half)
            with Image.open(img2) as im2:
                box2 = (0, 0, *im2.size)
            lower = shot.model_copy(update={"motion": shot.motion2 or shot.motion, "crop": shot.crop2})
            top.partner = prepare(lower, img2, box2, t, secs, [], "BeVietnamPro-Bold.ttf", i + 500 + seed,
                                  recipe.background, half)
            prepared.append(top)
        else:
            prepared.append(prepare(shot, img, box, t, secs, [], "BeVietnamPro-Bold.ttf", i + seed, recipe.background))
        t += secs
    timeline = Timeline(prepared, beats_video)
    total = timeline.seconds
    name = out_name or f"edit - {safe_name(track.title)} - {safe_name(track.artist)}.mp4"
    with tempfile.TemporaryDirectory(prefix="comic_folder_") as tmp:
        tmp = Path(tmp)
        picture = encode(timeline, tmp / "picture.mp4",
                         lambda n, tt, im: log(f"  {tt:5.1f}s / {total:.1f}s") if n % 300 == 0 else None)
        audio = music_lib.prepare_audio(track, start, total, tmp / "music.wav")
        final = mux(picture, audio, tmp / "video.mp4", total)
        out = folder / name
        shutil.copy2(final, out)
    return out


def sample_sheet(video: Path, out: Path, every: float = 1.5) -> Path:
    """A contact sheet of frames, for checking a rendered folder video."""
    import subprocess

    ff = music_lib.ffmpeg()
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run([ff, "-v", "error", "-i", str(video), "-vf", f"fps=1/{every},scale=216:384",
                        str(Path(tmp) / "f%03d.png")], check=True)
        frames = sorted(Path(tmp).glob("f*.png"))
        cols = 10
        rows = (len(frames) + cols - 1) // cols
        sheet = Image.new("RGB", (cols * 220, rows * 388), (40, 40, 40))
        for i, f in enumerate(frames):
            sheet.paste(Image.open(f), ((i % cols) * 220 + 2, (i // cols) * 388 + 2))
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out)
    return out


def assign(folders: list[Path], tracks: list, seed: int = 7) -> list[tuple[Path, str, str]]:
    """Spread recipes and tracks: no recipe twice in a row, every track used
    once before any repeats, each pick matching the recipe's moods when it can."""
    rng = random.Random(seed)
    names = list(RECIPES)
    uses = {t.id: 0 for t in tracks}
    out, last = [], None
    for i, folder in enumerate(folders):
        imgs = images(folder)
        tall = sum(1 for p in imgs if (lambda s: s[1] > s[0] * 1.5)(Image.open(p).size)) >= max(1, len(imgs) // 2)
        choices = [n for n in names if n != last and not RECIPES[n].captioned
                   and (n != "pan" or tall) and (n != "split" or len(imgs) >= 4)
                   and (not RECIPES[n].reuse or len(imgs) >= 4)]
        if folder.parent.name.lower() == "manga":
            # Black-and-white full pages: a full-frame crop throws half the page away.
            choices = [n for n in choices if RECIPES[n].fit is F.CONTAIN or n == "pan"] or choices
        if "meme" in folder.name.lower():
            choices = [n for n in choices if n in ("whip", "beat")] or choices
        r = choices[rng.randrange(len(choices))]
        last = r
        low = min(uses.values())
        pool = [t for t in tracks if uses[t.id] == low]
        matching = [t for t in pool if set(t.moods) & set(RECIPES[r].moods)]
        pick = rng.choice(matching or pool)
        uses[pick.id] += 1
        out.append((folder, r, pick.id))
    return out


def batch_state(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
