"""`comic clean`: flat fills on the spot, artwork spots batched, art outside untouched."""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from comic import clean_stage
from comic.paths import ChapterPaths
from comic.schemas import BBox, PageAnalysis, PageRecord, ReadingDirection, TextKind, TextRegion
from comic.state.store import ChapterStore, write_json
from comic.vision.analyze import save_mask


@pytest.fixture
def chapter(tmp_path, monkeypatch):
    monkeypatch.setenv("COMIC_HOME", str(tmp_path))
    paths = ChapterPaths("S", "Chapter_001")
    store = ChapterStore(paths)
    state = store.create(source_language="en")
    state.pages = [PageRecord(page=1, filename="001.png")]
    store.save(state)
    # left half plain white, right half a striped "artwork"; a black word on each
    img = np.full((200, 300, 3), 255, np.uint8)
    img[:, 150:, :] = np.where((np.arange(150) // 4 % 2)[None, :, None] == 0, 90, 200)
    mask = np.zeros((200, 300), np.uint8)
    for x0 in (30, 180):
        img[90:110, x0:x0 + 60] = 0
        mask[90:110, x0:x0 + 60] = 255
    Image.fromarray(img).save(paths.original / "001.png")
    save_mask(mask, paths.mask_file(1))
    regions = [
        TextRegion(id="p001_r01", page=1, bbox=BBox(x1=25, y1=85, x2=95, y2=115),
                   kind=TextKind.DIALOGUE, source_text="HI"),
        TextRegion(id="p001_r02", page=1, bbox=BBox(x1=175, y1=85, x2=245, y2=115),
                   kind=TextKind.NARRATION, source_text="ART", reading_order=1),
        TextRegion(id="p001_r03", page=1, bbox=BBox(x1=100, y1=150, x2=140, y2=180),
                   kind=TextKind.SFX, source_text="BAM", reading_order=2),
    ]
    write_json(paths.analysis_file(1), PageAnalysis(
        page=1, width=300, height=200, reading_direction=ReadingDirection.TTB, regions=regions))
    return paths, img


def test_batches_artwork_spots_and_keeps_art(chapter, monkeypatch):
    paths, img = chapter
    sent = {}

    def fake_inpaint(items, progress=None):
        sent["n"] = len(items)
        return {name: np.full_like(patch, 77) for name, patch, _hidden in items}

    monkeypatch.setattr(clean_stage, "_worker_alive", lambda: True)
    import comic.colab.tasks as tasks
    monkeypatch.setattr(tasks, "inpaint", fake_inpaint)
    out = clean_stage.clean_chapter(paths)
    assert out.where == "Colab T4" and sent["n"] == out.inpainted >= 1
    cleaned = np.array(Image.open(paths.cleaned / "001.png").convert("RGB"))
    assert (cleaned[95:105, 40:80] == 255).all()          # plain bubble: flat white
    assert (cleaned[95:105, 190:230] == 77).all()         # artwork spot: from the batch
    assert (cleaned[150:200, :] == img[150:200, :]).all()  # SFX untouched here (Photoshop's job)
    assert (cleaned[:60] == img[:60]).all()                # far art untouched


def test_falls_back_to_local_without_a_worker(chapter, monkeypatch):
    paths, _ = chapter
    monkeypatch.setattr(clean_stage, "_worker_alive", lambda: False)
    import comic.image_processing.clean as clean
    monkeypatch.setattr(clean, "_inpaint", lambda patch, hole, model: np.full_like(patch, 55))
    out = clean_stage.clean_chapter(paths)
    assert out.where == "local CPU"
    cleaned = np.array(Image.open(paths.cleaned / "001.png").convert("RGB"))
    assert (cleaned[95:105, 190:230] == 55).all()



def test_held_page_with_cleanup_is_cleaned_like_any_page(chapter, monkeypatch):
    """A page held for its content with --cleanup is cleaned fully (the operator letters it)."""
    paths, img = chapter
    store = ChapterStore(paths)
    state = store.load()
    state.pages[0].excluded = state.pages[0].held = True
    store.save(state)

    monkeypatch.setattr(clean_stage, "_worker_alive", lambda: False)
    import comic.image_processing.clean as clean
    monkeypatch.setattr(clean, "_inpaint", lambda patch, hole, model: np.full_like(patch, 55))
    assert clean_stage.clean_chapter(paths).pages == 0          # held without --cleanup: skipped

    state = store.load()
    state.pages[0].hold_cleanup = True
    store.save(state)
    assert clean_stage.clean_chapter(paths).pages == 1
    cleaned = np.array(Image.open(paths.cleaned / "001.png").convert("RGB"))
    assert (cleaned[95:105, 40:80] == 255).all()                # dialogue erased
    assert (cleaned[95:105, 190:230] == 55).all()               # lettering over art erased
