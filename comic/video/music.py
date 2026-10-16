"""The music library: what is licensed, its tempo and beats, and the cut.

Tracks live in ``assets/music`` with ``library.yaml`` naming each one's
licence, credit line and moods. Only tracks the operator may use go in
there -- never commercial songs, which belong over the silent version in the
TikTok app instead.

Beats come from librosa and are cached beside the tracks
(``.analysis/<id>.json``), so a track is analysed once.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from comic.paths import project_root


def music_dir() -> Path:
    return project_root() / "assets" / "music"


def ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    # winget installs per-user and only new shells see the PATH change.
    found = sorted((Path.home() / "AppData/Local/Microsoft/WinGet/Packages").glob("Gyan.FFmpeg*/*/bin/ffmpeg.exe"))
    if found:
        return str(found[-1])
    raise FileNotFoundError("ffmpeg not found -- winget install Gyan.FFmpeg")


@dataclass
class Track:
    id: str
    file: str
    title: str
    artist: str = ""
    license: str = ""
    credit: str | None = None
    source: str = ""
    moods: list[str] = field(default_factory=list)

    @property
    def path(self) -> Path:
        return music_dir() / self.file


def library() -> dict[str, Track]:
    f = music_dir() / "library.yaml"
    if not f.exists():
        return {}
    data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
    known = Track.__dataclass_fields__
    return {t["id"]: Track(**{k: v for k, v in t.items() if k in known}) for t in data.get("tracks", [])}


def analyse(track: Track) -> dict:
    """Duration, tempo and beat times (seconds), cached."""
    cache = music_dir() / ".analysis" / f"{track.id}.json"
    if cache.exists() and cache.stat().st_mtime >= track.path.stat().st_mtime:
        return json.loads(cache.read_text(encoding="utf-8"))
    import librosa
    import numpy as np

    # Decode through FFmpeg: TikTok serves AAC under an .mp3 name, which
    # libsndfile refuses.
    raw = subprocess.run([ffmpeg(), "-v", "error", "-i", str(track.path), "-ac", "1", "-ar", "22050",
                          "-f", "f32le", "-"], capture_output=True, check=True).stdout
    y, sr = np.frombuffer(raw, np.float32).copy(), 22050
    tempo, beats = librosa.beat.beat_track(y=y, sr=sr)
    rms = librosa.feature.rms(y=y)[0]
    # Loudness per second, so the agent can start where the track opens up.
    per_sec = [round(float(c.mean()), 4) for c in np.array_split(rms, max(1, int(len(y) / sr)))]
    out = {
        "duration": round(len(y) / sr, 2),
        "bpm": round(float(np.atleast_1d(tempo)[0]), 1),
        "beats": [round(float(t), 3) for t in librosa.frames_to_time(beats, sr=sr)],
        "energy": per_sec,
    }
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(out), encoding="utf-8")
    return out


def snap_cuts(durations: list[float], beats: list[float], start: float, tolerance: float = 0.25) -> list[float]:
    """Shot lengths moved so each cut lands on a beat of the track.

    ``beats`` are track times; the video starts at ``start``. A cut moves to
    the nearest beat within ``tolerance`` of the shot's own length; the
    total length is kept within a frame of the original.
    """
    if not beats:
        return list(durations)
    rel = [b - start for b in beats if b > start]
    cuts, t = [], 0.0
    for d in durations[:-1]:
        t += d
        near = min(rel, key=lambda b: abs(b - t), default=t)
        cuts.append(near if abs(near - t) <= tolerance * d else t)
    total = sum(durations)
    bounds = [0.0] + cuts + [total]
    snapped = [round(b - a, 3) for a, b in zip(bounds, bounds[1:])]
    # A snap must never collapse a shot.
    if min(snapped) < 0.3:
        return list(durations)
    return snapped


def prepare_audio(track: Track, start: float, seconds: float, out: Path, fade_out: float = 1.5) -> Path:
    """The track's section for the video: trimmed, faded, loudness-matched."""
    out.parent.mkdir(parents=True, exist_ok=True)
    flt = (f"afade=t=in:st=0:d=0.4,afade=t=out:st={max(0.0, seconds - fade_out):.2f}:d={fade_out},"
           "loudnorm=I=-14:TP=-1.5:LRA=11")
    subprocess.run([ffmpeg(), "-v", "error", "-y", "-ss", f"{start:.3f}", "-t", f"{seconds:.3f}",
                    "-i", str(track.path), "-af", flt, "-ar", "44100", "-ac", "2", str(out)], check=True)
    return out
