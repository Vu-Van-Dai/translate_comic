"""Colab as a GPU worker, reached through Google Drive (the operator, 2026-11-08).

No tunnel: ngrok's free plan caps traffic at 1 GB a month, and free Colab
runtimes may be cut for serving an API. Instead both sides share one Drive
folder, ``My Drive/comic_worker/``:

    code/       this project's ``comic`` package, pushed by ``comic colab push``
    models/     detector + OCR models, downloaded by the notebook itself once
    jobs/<id>/  one job: ``in/`` files, then ``job.json`` written LAST
                Colab answers with ``out/`` files, then ``done.json`` LAST
    worker.json heartbeat the notebook rewrites while its worker cell runs

Writing the manifest last, with every file's size in it, is what makes this
safe over a sync client: the other side waits until every listed file has
arrived at its full size before touching any of them.

Google Drive for desktop provides the local folder. It does not launch at
login (the operator forbids boot-time apps), so :func:`drive_root` starts it
on demand.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from comic.paths import project_root

#: Created only from the PC: when the notebook created its own folder before
#: the PC's upload landed, Drive kept two folders of one name and the PC's copy
#: vanished from view (2026-11-08). The notebook now only waits for this one.
FOLDER = "comic_worker"
#: Heartbeats older than this mean the notebook's worker cell is not running.
WORKER_STALE_S = 180


class ColabError(RuntimeError):
    pass


# --------------------------------------------------------------------- drive
def _drive_exe() -> Path | None:
    base = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Google" / "Drive File Stream"
    found = sorted(base.glob("*/GoogleDriveFS.exe"))
    return found[-1] if found else None


def _find_mount() -> Path | None:
    override = os.environ.get("COMIC_DRIVE")
    if override:
        return Path(override)
    for letter in "GHIJKLMNOPQRSTUVWXYZDEF":
        p = Path(f"{letter}:/My Drive")
        try:
            if p.is_dir():
                return p
        except OSError:
            continue
    return None


def drive_root(start: bool = True, wait_s: int = 90) -> Path:
    """``My Drive/comic_worker``, starting Google Drive for desktop if needed."""
    mount = _find_mount()
    if mount is None and start:
        exe = _drive_exe()
        if exe is None:
            raise ColabError("Google Drive for desktop is not installed "
                             "(https://www.google.com/drive/download/)")
        subprocess.Popen([str(exe)], close_fds=True,
                         creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
        deadline = time.time() + wait_s
        while mount is None and time.time() < deadline:
            time.sleep(3)
            mount = _find_mount()
    if mount is None:
        raise ColabError("Google Drive is not mounted (set COMIC_DRIVE to its 'My Drive' folder)")
    root = mount / FOLDER
    (root / "jobs").mkdir(parents=True, exist_ok=True)
    return root


def stop_drive() -> None:
    """Close Google Drive for desktop after work (nothing stays running)."""
    subprocess.run(["taskkill", "/IM", "GoogleDriveFS.exe", "/F"],
                   capture_output=True, check=False)


# --------------------------------------------------------------------- push
def _mirror(src: Path, dst: Path, patterns: tuple[str, ...]) -> int:
    """Copy files matching ``patterns`` whose size or mtime differ; return count."""
    copied = 0
    for pattern in patterns:
        for f in src.rglob(pattern):
            if "__pycache__" in f.parts:
                continue
            target = dst / f.relative_to(src)
            if target.exists() and target.stat().st_size == f.stat().st_size \
                    and target.stat().st_mtime >= f.stat().st_mtime - 2:
                # Drive keeps mtimes to the millisecond; without the slack every
                # push re-uploaded the 425 MB of models.
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, target)
            copied += 1
    return copied


def push() -> dict:
    """Send the current code to Drive for the notebook.

    Models are NOT pushed: uploading 425 MB from a home connection took far
    longer than Colab fetching them itself (the notebook runs
    ``scripts/fetch_models.py`` into ``comic_worker/models`` once and reuses it).

    Writes ``pushed.json`` listing every code file and its size: Drive for
    desktop uploads in its own order and Colab's mount notices new folders
    late, so the notebook waits until everything listed has arrived in full.
    """
    root = drive_root()
    here = project_root()
    out = {"code": _mirror(here / "comic", root / "code" / "comic",
                           ("*.py", "*.yaml", "static/*"))}
    scripts = root / "code" / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    src = here / "scripts" / "fetch_models.py"
    if not (scripts / src.name).exists() or (scripts / src.name).read_bytes() != src.read_bytes():
        shutil.copyfile(src, scripts / src.name)
        out["code"] += 1
    files: dict[str, int] = {}
    base = root / "code"
    for f in base.rglob("*"):
        if f.is_file() and "__pycache__" not in f.parts:
            files[f"code/{f.relative_to(base).as_posix()}"] = f.stat().st_size
    (root / "pushed.json").write_text(
        json.dumps({"at": time.time(), "files": files}), encoding="utf-8")
    out["listed"] = len(files)
    return out


# --------------------------------------------------------------------- jobs
@dataclass
class Job:
    id: str
    dir: Path

    @property
    def done_file(self) -> Path:
        return self.dir / "done.json"


def worker_status() -> dict | None:
    f = drive_root() / "worker.json"
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    data["age_s"] = round(time.time() - float(data.get("t", 0)))
    data["alive"] = data["age_s"] < WORKER_STALE_S
    return data


def submit(task: str, files: dict[str, Path], params: dict | None = None) -> Job:
    """Upload ``files`` (relative name -> local path) and post the job."""
    root = drive_root()
    job_id = time.strftime("%Y%m%d-%H%M%S") + "-" + task + "-" + uuid.uuid4().hex[:6]
    job_dir = root / "jobs" / job_id
    sizes: dict[str, int] = {}
    for rel, src in files.items():
        target = job_dir / "in" / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, target)
        sizes[rel.replace("\\", "/")] = src.stat().st_size
    manifest = {"task": task, "files": sizes, "params": params or {}, "posted": time.time()}
    (job_dir / "job.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return Job(job_id, job_dir)


def wait(job: Job, timeout_s: int = 3600, progress=None) -> dict:
    """Block until Colab answers and every output file has arrived in full."""
    deadline = time.time() + timeout_s
    warned = False
    while time.time() < deadline:
        if job.done_file.exists():
            try:
                done = json.loads(job.done_file.read_text(encoding="utf-8"))
            except ValueError:
                done = None
            if done is not None:
                missing = [rel for rel, size in done.get("files", {}).items()
                           if not (job.dir / "out" / rel).exists()
                           or (job.dir / "out" / rel).stat().st_size != size]
                if not missing:
                    if not done.get("ok"):
                        raise ColabError(f"Colab job {job.id} failed:\n{done.get('error', '?')}")
                    return done
                if progress:
                    progress(f"downloading results ({len(missing)} files left)")
        elif not warned and time.time() - _posted(job) > 120:
            status = worker_status()
            if not status or not status["alive"]:
                warned = True
                if progress:
                    progress("no live Colab worker -- open colab/comic_gpu.ipynb and Run all")
        time.sleep(4)
    raise ColabError(f"timed out waiting for Colab job {job.id}")


def _posted(job: Job) -> float:
    try:
        return float(json.loads((job.dir / "job.json").read_text(encoding="utf-8"))["posted"])
    except (OSError, ValueError, KeyError):
        return time.time()


def outputs(job: Job) -> Path:
    return job.dir / "out"


def remove(job: Job) -> None:
    """Drop a finished job from Drive (the free tier has 15 GB)."""
    shutil.rmtree(job.dir, ignore_errors=True)
