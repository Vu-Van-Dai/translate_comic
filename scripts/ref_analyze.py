"""Measure reference edits: shot lengths, cut rhythm, tempo, and a frame sheet.

    python scripts/ref_analyze.py workspace/_references/tiktok

For each video: duration, size, cut times (frame-difference scene detection
on a small greyscale copy), shot-length stats, the music's tempo and how many
cuts land on a beat, plus `<id>_sheet.png` -- one frame per shot and one per
second -- so the edit can be looked at, not only counted. Writes
`summary.json` beside the videos. Read-only on the videos themselves.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

W, H, FPS = 90, 160, 10
#: Mean absolute difference (0-255) between consecutive small frames that
#: counts as a cut. A pan or zoom moves far less than this per 0.1 s.
CUT_DIFF = 28.0


def ffmpeg() -> str:
    from comic.video.music import ffmpeg as find
    try:
        return find()
    except FileNotFoundError:
        sys.exit("ffmpeg not found -- winget install Gyan.FFmpeg")


def frames(video: Path) -> np.ndarray:
    cmd = [ffmpeg(), "-v", "error", "-i", str(video), "-vf",
           f"fps={FPS},scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2",
           "-f", "rawvideo", "-pix_fmt", "gray", "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, H, W).astype(np.float32)


def cuts(gray: np.ndarray) -> list[float]:
    diff = np.abs(np.diff(gray, axis=0)).mean(axis=(1, 2))
    out, last = [], -1.0
    for i, d in enumerate(diff):
        t = (i + 1) / FPS
        if d > CUT_DIFF and t - last > 0.25:
            out.append(round(t, 2))
            last = t
    return out


def tempo(video: Path) -> tuple[float | None, list[float]]:
    try:
        import librosa
    except ImportError:
        return None, []
    wav = video.with_suffix(".tmp.wav")
    subprocess.run([ffmpeg(), "-v", "error", "-y", "-i", str(video), "-ac", "1", "-ar", "22050", str(wav)],
                   check=False)
    if not wav.exists():
        return None, []
    try:
        y, sr = librosa.load(str(wav), sr=22050)
        bpm, beats = librosa.beat.beat_track(y=y, sr=sr)
        return float(np.atleast_1d(bpm)[0]), [round(float(t), 2) for t in librosa.frames_to_time(beats, sr=sr)]
    finally:
        wav.unlink(missing_ok=True)


def sheet(video: Path, times: list[float], out: Path) -> None:
    thumbs = []
    for t in times:
        cmd = [ffmpeg(), "-v", "error", "-ss", f"{t:.2f}", "-i", str(video), "-frames:v", "1",
               "-vf", "scale=216:384:force_original_aspect_ratio=decrease,pad=216:384:(ow-iw)/2:(oh-ih)/2",
               "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
        raw = subprocess.run(cmd, capture_output=True).stdout
        if len(raw) != 216 * 384 * 3:
            continue
        im = Image.frombytes("RGB", (216, 384), raw)
        ImageDraw.Draw(im).text((4, 4), f"{t:.1f}s", fill=(255, 0, 0))
        thumbs.append(im)
    cols = 8
    rows = (len(thumbs) + cols - 1) // cols
    canvas = Image.new("RGB", (cols * 220, max(1, rows) * 388), "grey")
    for i, im in enumerate(thumbs):
        canvas.paste(im, ((i % cols) * 220, (i // cols) * 388))
    canvas.save(out)


def analyse(video: Path) -> dict:
    probe = json.loads(subprocess.run(
        [str(Path(ffmpeg()).with_name("ffprobe.exe")) if Path(ffmpeg()).suffix else "ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(video)],
        capture_output=True, text=True, encoding="utf-8", errors="replace").stdout)
    vs = next(s for s in probe["streams"] if s["codec_type"] == "video")
    dur = float(probe["format"]["duration"])
    gray = frames(video)
    cut_times = cuts(gray)
    bounds = [0.0] + cut_times + [dur]
    shots = [round(b - a, 2) for a, b in zip(bounds, bounds[1:]) if b - a > 0.05]
    bpm, beats = tempo(video)
    on_beat = sum(1 for c in cut_times if beats and min(abs(c - b) for b in beats) <= 0.12)
    mids = [round((a + b) / 2, 2) for a, b in zip(bounds, bounds[1:])]
    sheet(video, sorted(set(mids + [float(s) for s in range(int(dur))])), video.with_name(video.stem + "_sheet.png"))
    meta = video.with_suffix(".json")
    return {
        "id": video.stem,
        "meta": json.loads(meta.read_text(encoding="utf-8")) if meta.exists() else {},
        "size": f"{vs['width']}x{vs['height']}",
        "fps": vs.get("r_frame_rate"),
        "duration": round(dur, 2),
        "cuts": cut_times,
        "shots": len(shots),
        "shot_mean": round(float(np.mean(shots)), 2) if shots else None,
        "shot_min": min(shots) if shots else None,
        "shot_max": max(shots) if shots else None,
        "bpm": round(bpm, 1) if bpm else None,
        "cuts_on_beat": f"{on_beat}/{len(cut_times)}",
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--again", action="store_true", help="re-analyse videos that already have a sheet")
    args = ap.parse_args()
    root = Path(args.folder)
    results = []
    for video in sorted(root.rglob("*.mp4")):
        if video.with_name(video.stem + "_sheet.png").exists() and not args.again:
            continue
        r = analyse(video)
        results.append(r)
        print(f"{r['id']}: {r['duration']}s {r['size']} shots={r['shots']} mean={r['shot_mean']}s "
              f"[{r['shot_min']}-{r['shot_max']}] bpm={r['bpm']} on-beat={r['cuts_on_beat']}", flush=True)
    old = json.loads((root / "summary.json").read_text(encoding="utf-8")) if (root / "summary.json").exists() else []
    seen = {r["id"] for r in results}
    results += [r for r in old if r["id"] not in seen]
    (root / "summary.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
