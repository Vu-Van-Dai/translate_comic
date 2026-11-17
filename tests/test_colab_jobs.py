"""The Drive job protocol, with the Colab worker loop running in a thread."""

from __future__ import annotations

import json
import threading

import pytest

from comic.colab import jobs, worker


@pytest.fixture
def drive(tmp_path, monkeypatch):
    mount = tmp_path / "My Drive"
    mount.mkdir()
    monkeypatch.setenv("COMIC_DRIVE", str(mount))
    monkeypatch.setattr(worker, "_beat", lambda root, stop: None)
    return mount / jobs.FOLDER


def _echo(job_dir, spec):
    text = (job_dir / "in" / "a.txt").read_text()
    out = job_dir / "out"
    out.mkdir()
    (out / "b.txt").write_text(text.upper())
    return {"b.txt": len(text)}, {"n": spec["params"]["n"]}


def _boom(job_dir, spec):
    raise ValueError("nope")


def _start(monkeypatch):
    monkeypatch.setitem(worker.TASKS, "echo", _echo)
    monkeypatch.setitem(worker.TASKS, "boom", _boom)
    root = jobs.drive_root(start=False)
    threading.Thread(target=worker.main, args=(root, 0.05), daemon=True).start()


def test_round_trip(drive, tmp_path, monkeypatch):
    src = tmp_path / "a.txt"
    src.write_text("hello")
    _start(monkeypatch)
    job = jobs.submit("echo", {"a.txt": src}, {"n": 3})
    done = jobs.wait(job, timeout_s=20)
    assert done["summary"]["n"] == 3
    assert "numpy" in done["summary"]["env"]       # versions reported with every job
    assert (jobs.outputs(job) / "b.txt").read_text() == "HELLO"
    jobs.remove(job)
    assert not job.dir.exists()


def test_failure_is_reported_not_fatal(drive, tmp_path, monkeypatch):
    src = tmp_path / "a.txt"
    src.write_text("x")
    _start(monkeypatch)
    with pytest.raises(jobs.ColabError, match="nope"):
        jobs.wait(jobs.submit("boom", {"a.txt": src}), timeout_s=20)
    # the worker survived and still serves the next job
    assert jobs.wait(jobs.submit("echo", {"a.txt": src}, {"n": 1}), timeout_s=20)["ok"]


def test_worker_waits_for_every_file(drive, tmp_path):
    job_dir = drive / "jobs" / "j1"
    (job_dir / "in").mkdir(parents=True)
    (job_dir / "in" / "a.txt").write_text("hel")  # still syncing: 3 of 5 bytes
    spec = {"files": {"a.txt": 5}}
    assert not worker._arrived(job_dir, spec["files"])
    (job_dir / "in" / "a.txt").write_text("hello")
    assert worker._arrived(job_dir, spec["files"])


def test_wait_ignores_partial_outputs(drive, tmp_path):
    jobs.drive_root(start=False)
    src = tmp_path / "a.txt"
    src.write_text("x")
    job = jobs.submit("echo", {"a.txt": src})
    (job.dir / "out").mkdir()
    (job.dir / "out" / "b.txt").write_text("12")
    (job.dir / "done.json").write_text(json.dumps({"ok": True, "files": {"b.txt": 4}}))
    with pytest.raises(jobs.ColabError, match="timed out"):
        jobs.wait(job, timeout_s=1)


def test_inpaint_touches_only_the_mask(monkeypatch):
    np = pytest.importorskip("numpy")
    torch = pytest.importorskip("torch")
    seen = {}

    def fake(img, mask):  # paints the whole crop grey
        seen["shape"] = tuple(img.shape)
        return torch.full_like(img, 0.5)

    monkeypatch.setattr(worker, "_lama", lambda: fake)
    image = np.zeros((300, 200, 3), np.uint8)
    image[:, :, 0] = 200
    mask = np.zeros((300, 200), bool)
    mask[100:120, 50:90] = True
    out = worker.inpaint_array(image, mask)
    assert (out[mask] == 128).all()                 # inside: model output
    assert (out[~mask] == image[~mask]).all()       # outside: untouched
    assert seen["shape"][2] % 8 == 0 and seen["shape"][3] % 8 == 0
    assert seen["shape"][2] < 300                   # cropped to the mask + margin


def test_batched_spots_match_one_at_a_time(monkeypatch):
    """2026-11-13: spots of similar size share one GPU call. The result must be
    exactly what painting each spot alone gives."""
    np = pytest.importorskip("numpy")
    torch = pytest.importorskip("torch")
    calls = []

    def fake(img, mask):   # deterministic, per-sample: each pixel -> its sample's mean
        calls.append(img.shape[0])
        return img.mean(dim=(1, 2, 3), keepdim=True).expand_as(img).clone()

    monkeypatch.setattr(worker, "_lama", lambda: fake)
    rng = np.random.default_rng(0)
    pairs = []
    for k in range(5):
        image = rng.integers(0, 255, (260, 240, 3), dtype=np.uint8)
        mask = np.zeros((260, 240), bool)
        mask[100 + k:130 + k, 60:100] = True
        pairs.append((image, mask))
    batched = worker.inpaint_batch(pairs)
    assert max(calls) == 5                            # one call for the whole bucket
    calls.clear()
    singles = [worker.inpaint_array(i, m) for i, m in pairs]
    assert all((a == b).all() for a, b in zip(batched, singles))
    assert all((b[~m] == i[~m]).all() for b, (i, m) in zip(batched, pairs))


def test_out_of_memory_halves_the_batch(monkeypatch):
    np = pytest.importorskip("numpy")
    torch = pytest.importorskip("torch")
    sizes = []

    def fake(img, mask):
        sizes.append(img.shape[0])
        if img.shape[0] > 1:
            raise RuntimeError("CUDA out of memory. Tried to allocate 2.00 GiB")
        return torch.full_like(img, 0.5)

    monkeypatch.setattr(worker, "_lama", lambda: fake)
    pairs = []
    for k in range(4):
        image = np.zeros((200, 200, 3), np.uint8)
        mask = np.zeros((200, 200), bool)
        mask[80:100, 80:120] = True
        pairs.append((image, mask))
    out = worker.inpaint_batch(pairs)
    assert sizes[0] == 4 and sizes.count(1) == 4
    assert all((o[m] == 128).all() for o, (_, m) in zip(out, pairs))


def test_batch_padding_is_never_context(monkeypatch):
    """2026-11-13: mirrored padding marked as known context copied the letters
    being erased beside the hole, and LaMa painted their ghost back. Whatever
    pads a crop up to its bucket is marked as hole."""
    np = pytest.importorskip("numpy")
    torch = pytest.importorskip("torch")
    seen = {}

    def fake(img, mask):
        seen["mask"] = mask.cpu().numpy()
        return torch.full_like(img, 0.5)

    monkeypatch.setattr(worker, "_lama", lambda: fake)
    image = np.zeros((150, 170, 3), np.uint8)
    mask = np.zeros((150, 170), bool)
    mask[60:80, 60:100] = True
    worker.inpaint_batch([(image, mask)])
    m = seen["mask"][0, 0]
    prep = worker._prepare(image, mask)
    sh, sw = prep["small_m"].shape
    assert m.shape[0] > sh or m.shape[1] > sw          # it was padded
    assert (m[sh:, :] == 1).all() and (m[:, sw:] == 1).all()
