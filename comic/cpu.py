"""Keep the pipeline from taking the whole machine.

A render used to start cores-minus-one workers, each with libraries that by
default spawn a thread per core (onnxruntime, OpenVINO, OpenCV, torch) -- 7 x 8
threads on an 8-core laptop, CPU pinned at 100% and the desktop lagging (the
operator's report, 2026-10-12). Two rules now hold for every entry point:

- **One thread budget for the whole run**, ``COMIC_CPU_SHARE`` of the logical
  cores (default 0.75: the operator allows 80-90% load, never above 90%),
  split between the worker processes; each library in a worker is held to
  its share.
- **Below-normal priority.** Windows hands the CPU to interactive programs
  first; worker processes inherit the class from the parent.

``COMIC_WORKERS=N`` fixes the process count; ``COMIC_CPU_SHARE=0.5`` leaves
more of the machine free while someone is working on it.
"""

from __future__ import annotations

import os
import sys

#: Libraries that size their thread pools from these at import time.
_THREAD_ENV = ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS",
               "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS")


def total_threads() -> int:
    """Threads the whole run may keep busy at once."""
    cores = os.cpu_count() or 2
    try:
        share = float(os.environ.get("COMIC_CPU_SHARE", "0.75"))
    except ValueError:
        share = 0.75
    share = min(1.0, max(0.1, share))
    return max(1, int(cores * share))


def workers() -> int:
    """Worker processes a stage may start (each holds its own models, ~1 GB)."""
    override = os.environ.get("COMIC_WORKERS", "").strip()
    if override.isdigit() and int(override) > 0:
        return min(int(override), total_threads())
    return max(1, total_threads() // 2)


def threads_per_worker() -> int:
    return max(1, total_threads() // workers())


def throttle() -> None:
    """Lower priority and cap library thread pools. Call before heavy imports."""
    per = str(threads_per_worker())
    for name in _THREAD_ENV:
        os.environ.setdefault(name, per)
    if sys.platform == "win32":
        try:
            import ctypes

            BELOW_NORMAL_PRIORITY_CLASS = 0x4000
            kernel = ctypes.windll.kernel32
            kernel.SetPriorityClass(kernel.GetCurrentProcess(), BELOW_NORMAL_PRIORITY_CLASS)
        except (AttributeError, OSError):
            pass
    try:
        import cv2

        cv2.setNumThreads(int(per))
    except ImportError:
        pass


def ort_options(options=None):
    """onnxruntime ignores OMP_NUM_THREADS; cap its pools explicitly."""
    import onnxruntime as ort

    options = options or ort.SessionOptions()
    options.intra_op_num_threads = threads_per_worker()
    options.inter_op_num_threads = 1
    return options
