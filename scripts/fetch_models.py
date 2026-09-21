"""Download the ONNX weights the vision stages need.

Weights live outside git (see .gitignore) and are fetched on demand. Downloads go
to a temp file and are renamed only after the size check passes, so an interrupted
run cannot leave a truncated model that fails cryptically at inference time.

Usage:
    python scripts/fetch_models.py [--only NAME] [--force]
"""

from __future__ import annotations

import argparse
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from comic.console import console, setup_console  # noqa: E402
from comic.paths import models_dir  # noqa: E402

USER_AGENT = "comic-translator/0.1 (+model bootstrap)"


@dataclass(frozen=True)
class ModelSpec:
    name: str
    url: str
    purpose: str
    required: bool
    #: Minimum plausible size; guards against an HTML error page being saved.
    min_bytes: int
    #: Named post-download conversion, when the published file is not the
    #: shape we need. Applied before the file is moved into place.
    transform: str | None = None
    #: Floor for the file once installed. It differs from ``min_bytes``
    #: whenever a transform runs, because that guards the *download* while
    #: this decides whether what is already on disk can be skipped -- and a
    #: transform that shrinks the file past ``min_bytes`` would otherwise make
    #: every run re-download it.
    min_installed: int | None = None

    @property
    def installed_floor(self) -> int:
        return self.min_bytes if self.min_installed is None else self.min_installed


MANIFEST: list[ModelSpec] = [
    ModelSpec(
        name="comictextdetector.onnx",
        url=(
            "https://github.com/zyddnys/manga-image-translator/releases/download/"
            "beta-0.3/comictextdetector.pt.onnx"
        ),
        purpose="text-line boxes + segmentation mask + bubble flag, in one pass",
        required=True,
        min_bytes=10_000_000,
    ),
    ModelSpec(
        name="inpainting_lama.onnx",
        url="https://huggingface.co/Carve/LaMa-ONNX/resolve/main/lama_fp32.onnx",
        purpose="tier-2 inpainting for text over artwork",
        required=False,
        min_bytes=10_000_000,
    ),
    ModelSpec(
        name="korean_PP-OCRv5_rec.onnx",
        url=(
            "https://huggingface.co/PaddlePaddle/korean_PP-OCRv5_mobile_rec_onnx/"
            "resolve/main/inference.onnx"
        ),
        purpose="Korean recognition; RapidOCR ships Chinese models only",
        required=False,
        min_bytes=5_000_000,
    ),
    ModelSpec(
        name="korean_PP-OCRv5_rec_keys.txt",
        url=(
            "https://huggingface.co/PaddlePaddle/korean_PP-OCRv5_mobile_rec_onnx/"
            "resolve/main/inference.yml"
        ),
        purpose="the 11945-entry charset that model decodes into",
        required=False,
        min_bytes=50_000,
        transform="paddle_charset",
        min_installed=40_000,
    ),
]


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def download(spec: ModelSpec, dest: Path) -> int:
    """Stream to a temp file, validate, then atomically rename into place."""
    tmp = dest.with_suffix(dest.suffix + ".part")
    request = urllib.request.Request(spec.url, headers={"User-Agent": USER_AGENT})
    written = 0
    # Carriage-return progress only makes sense on a terminal; when output is
    # redirected it produces one line per megabyte and buries everything else.
    show_progress = sys.stdout.isatty()
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            total = int(response.headers.get("Content-Length") or 0)
            with tmp.open("wb") as fh:
                while chunk := response.read(1 << 20):
                    fh.write(chunk)
                    written += len(chunk)
                    if total and show_progress:
                        pct = 100 * written / total
                        print(
                            f"  {spec.name}: {human(written)} / {human(total)} ({pct:.0f}%)",
                            end="\r",
                            flush=True,
                        )
        if show_progress:
            print(" " * 70, end="\r", flush=True)
        if written < spec.min_bytes:
            raise ValueError(
                f"got {human(written)}, expected at least {human(spec.min_bytes)} "
                f"-- the URL probably returned an error page"
            )
        if spec.transform:
            written = TRANSFORMS[spec.transform](tmp)
        tmp.replace(dest)
        return written
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def paddle_charset(path: Path) -> int:
    """Turn a PaddleOCR ``inference.yml`` into the keys file RapidOCR wants.

    The official export keeps its charset in the config rather than in the
    ONNX metadata, which is where RapidOCR's own conversions put it. Rewriting
    it here means the model drops straight into ``rec_keys_path`` instead of
    every caller having to know the difference.
    """
    import yaml

    config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    characters = (config.get("PostProcess") or {}).get("character_dict")
    if not characters:
        raise ValueError("no PostProcess.character_dict in the downloaded config")
    text = "\n".join(characters)
    path.write_text(text, encoding="utf-8")
    return len(text.encode("utf-8"))


TRANSFORMS = {"paddle_charset": paddle_charset}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", help="Fetch models whose name contains this.")
    parser.add_argument("--force", action="store_true", help="Re-download existing models.")
    args = parser.parse_args()

    setup_console()
    target = models_dir()
    target.mkdir(parents=True, exist_ok=True)

    specs = [
        s for s in MANIFEST
        if not args.only or args.only.lower() in s.name.lower()
    ]
    if not specs:
        console.print(f"[fail]no model named {args.only!r}[/fail]")
        console.print(f"known: {', '.join(s.name for s in MANIFEST)}")
        return 1

    ok, skipped, failed = 0, 0, 0
    for spec in specs:
        dest = target / spec.name
        if dest.exists() and dest.stat().st_size >= spec.installed_floor and not args.force:
            console.print(f"[path]  skip[/path] {spec.name} ({human(dest.stat().st_size)})")
            skipped += 1
            continue
        console.print(f"[stage]  get [/stage] {spec.name} -- {spec.purpose}")
        try:
            size = download(spec, dest)
        except (urllib.error.URLError, ValueError, OSError) as exc:
            tag = "fail" if spec.required else "warn"
            console.print(f"[{tag}]  {tag}[/{tag}] {spec.name}: {exc}")
            failed += 1 if spec.required else 0
            continue
        console.print(f"[ok]    ok[/ok] {spec.name} ({human(size)})")
        ok += 1

    console.print()
    console.print(f"downloaded {ok}, skipped {skipped}, failed {failed}")
    console.print(f"[path]{target}[/path]")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
