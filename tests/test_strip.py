"""Re-cutting a long strip into pages.

The fixture reproduces what a webtoon source actually delivers: panels of
varying height separated by gutters, lettering inside bubbles, and the whole
thing exported in a *fixed* step that lands wherever it lands. Every property
worth guaranteeing is geometric, so a synthetic strip tests them exactly as a
real one would.
"""

from __future__ import annotations

from functools import lru_cache

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from comic.vision.strip import (
    FLAT_SPREAD,
    MAX_HEIGHT,
    MIN_HEIGHT,
    flat_bands,
    gutter_level,
    needs_slicing,
    plan_cuts,
    row_level,
    row_spread,
    slice_strip,
)

WIDTH = 800
EXPORT_STEP = 1500


@lru_cache(maxsize=4)
def build_strip(seed: int = 7, panels: int = 30):
    """A webtoon strip, plus the row ranges that are genuinely gutter or bubble.

    Cached: building it costs a second of Gaussian blur and no test mutates it.
    """
    rng = np.random.default_rng(seed)
    rows, gutters, bubbles = [], [], []
    y = 0
    for _ in range(panels):
        gap = int(rng.integers(25, 70))
        rows.append(np.full((gap, WIDTH, 3), 255, np.uint8))
        gutters.append((y, y + gap))
        y += gap

        height = int(rng.integers(500, 1500))
        panel = rng.integers(40, 220, (height, WIDTH, 3), dtype=np.uint8)
        panel = cv2.GaussianBlur(panel, (31, 31), 0)
        middle = height // 2
        cv2.ellipse(panel, (WIDTH // 2, middle), (220, 110), 0, 0, 360, (255,) * 3, -1)
        cv2.ellipse(panel, (WIDTH // 2, middle), (220, 110), 0, 0, 360, (0,) * 3, 3)
        for line in range(3):
            cv2.putText(panel, "AAAAAAAAA", (WIDTH // 2 - 180, middle - 40 + line * 40),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 0), 3)
        rows.append(panel)
        bubbles.append((y + middle - 110, y + middle + 110))
        y += height

    rows.append(np.full((40, WIDTH, 3), 255, np.uint8))
    gutters.append((y, y + 40))
    return np.vstack(rows), gutters, bubbles


def export(strip, directory, step: int = EXPORT_STEP):
    """Save the strip the way an uploader does: a fixed step, not pages."""
    directory.mkdir(parents=True, exist_ok=True)
    written = []
    for number, top in enumerate(range(0, strip.shape[0], step), start=1):
        path = directory / f"{number:03d}.png"
        ok, buffer = cv2.imencode(".png", strip[top:top + step])
        assert ok
        path.write_bytes(buffer.tobytes())
        written.append(path)
    return written


def export_at(strip, gutters, directory):
    """Save the strip cut at real gutters: already pages, nothing to re-cut."""
    directory.mkdir(parents=True, exist_ok=True)
    cuts = [0] + [(a + b) // 2 for a, b in gutters[1:]] + [strip.shape[0]]
    cuts = sorted(set(cuts))
    written = []
    for number, (top, bottom) in enumerate(zip(cuts, cuts[1:]), start=1):
        path = directory / f"{number:03d}.png"
        ok, buffer = cv2.imencode(".png", strip[top:bottom])
        assert ok
        path.write_bytes(buffer.tobytes())
        written.append(path)
    return written


# ------------------------------------------------------------------ measuring


def test_a_row_holding_lettering_is_never_flat():
    """The safety guarantee: ink contrasts, so its row cannot pass the test.

    Everything else here rests on this. If a lettered row could read as flat,
    a seam could be chosen through a line of dialogue.
    """
    strip, _, bubbles = build_strip()
    spread = row_spread(strip)
    for top, bottom in bubbles:
        assert spread[top:bottom].max() > FLAT_SPREAD


def test_flat_bands_finds_each_gutter_once():
    strip, gutters, _ = build_strip()
    bands = flat_bands(row_spread(strip))
    found = {(a, b) for a, b in bands}
    for gutter in gutters:
        assert any(a <= gutter[0] and b >= gutter[1] for a, b in found)


def test_gutter_level_is_measured_not_assumed_white():
    """A chapter letterboxed on black must not be told its gutters are white."""
    strip, _, _ = build_strip()
    dark = 255 - strip
    spread, level = row_spread(dark), row_level(dark)
    assert gutter_level(spread, level) == pytest.approx(0.0, abs=2)


# -------------------------------------------------------------------- cutting


def test_no_seam_falls_inside_a_bubble():
    strip, _, bubbles = build_strip()
    _, seams = plan_cuts(row_spread(strip), row_level(strip))
    assert seams
    for seam in seams:
        assert not any(top <= seam.y < bottom for top, bottom in bubbles)


def test_every_seam_lands_in_a_real_gutter():
    strip, gutters, _ = build_strip()
    _, seams = plan_cuts(row_spread(strip), row_level(strip))
    for seam in seams:
        assert any(top <= seam.y < bottom for top, bottom in gutters), seam


def test_pages_stay_within_the_height_bounds():
    strip, _, _ = build_strip()
    spans, _ = plan_cuts(row_spread(strip), row_level(strip))
    for top, bottom in spans:
        assert MIN_HEIGHT <= bottom - top <= MAX_HEIGHT


def test_spans_tile_the_strip_exactly():
    """No row is dropped and none is duplicated."""
    strip, _, _ = build_strip()
    spans, _ = plan_cuts(row_spread(strip), row_level(strip))
    assert spans[0][0] == 0
    assert spans[-1][1] == strip.shape[0]
    for (_, end), (start, _) in zip(spans, spans[1:]):
        assert end == start


def test_a_strip_with_no_gutters_is_cut_anyway_and_says_so():
    """Refusing would leave a page no later stage can handle."""
    rng = np.random.default_rng(3)
    noisy = rng.integers(0, 255, (9000, WIDTH, 3), dtype=np.uint8)
    spans, seams = plan_cuts(row_spread(noisy), row_level(noisy))
    assert len(spans) > 1
    assert all(not seam.safe for seam in seams)


# ------------------------------------------------------------------- deciding


def test_a_fixed_step_export_is_recognised_as_a_strip(tmp_path):
    """The case slicing exists for: files shorter than a page, cut mid-panel."""
    strip, _, _ = build_strip()
    sources = export(strip, tmp_path / "original")
    assert all(cv2.imdecode(np.fromfile(str(p), np.uint8), cv2.IMREAD_COLOR).shape[0]
               <= EXPORT_STEP for p in sources)
    assert needs_slicing(sources) is True


def test_a_source_already_cut_at_gutters_is_left_alone(tmp_path):
    strip, gutters, _ = build_strip()
    sources = export_at(strip, gutters, tmp_path / "original")
    assert needs_slicing(sources) is False


# ------------------------------------------------------------------- emitting


def test_slicing_reassembles_the_strip_pixel_for_pixel(tmp_path):
    """Re-cutting must move boundaries, never alter a pixel."""
    strip, _, _ = build_strip()
    sources = export(strip, tmp_path / "original")
    report = slice_strip(sources, tmp_path / "pages")

    assert report.page_count == len(report.spans)
    assert report.forced == 0
    rebuilt = np.vstack([
        cv2.imdecode(np.fromfile(str(tmp_path / "pages" / f"{n:03d}.png"), np.uint8),
                     cv2.IMREAD_COLOR)
        for n in range(1, report.page_count + 1)
    ])
    assert rebuilt.shape == strip.shape
    assert np.array_equal(rebuilt, strip)


def test_slicing_clears_a_previous_run(tmp_path):
    """A second pass that yields fewer pages must not leave the extras behind."""
    strip, _, _ = build_strip()
    sources = export(strip, tmp_path / "original")
    pages = tmp_path / "pages"
    slice_strip(sources, pages)
    stale = pages / "999.png"
    stale.write_bytes((pages / "001.png").read_bytes())
    report = slice_strip(sources, pages)
    assert not stale.exists()
    assert len(list(pages.glob("*.png"))) == report.page_count


def test_an_odd_width_slice_is_rescaled_not_rejected(tmp_path):
    strip, _, _ = build_strip(panels=6)
    directory = tmp_path / "original"
    sources = export(strip, directory)
    odd = cv2.resize(
        cv2.imdecode(np.fromfile(str(sources[1]), np.uint8), cv2.IMREAD_COLOR),
        (WIDTH // 2, 1500),
    )
    ok, buffer = cv2.imencode(".png", odd)
    assert ok
    sources[1].write_bytes(buffer.tobytes())

    report = slice_strip(sources, tmp_path / "pages")
    assert report.width == WIDTH
    assert report.resized == 1
    assert report.page_count >= 1
