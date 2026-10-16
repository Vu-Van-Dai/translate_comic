"""``comic video`` -- short vertical videos made from a chapter's art.

An add-on to the translation pipeline, never part of it. It reads a chapter's
text-free art (the Photoshop "Cleaned" layer, or the pipeline's cleaned pages,
or the raw pages) and never its translated pages: a video must not show
translated dialogue (the operator's rule, enforced in ``source.py``).

The split is the project's usual one. Deterministic tools cut pages into
panels, render frames and encode; the agent chooses -- which panels, in what
order, for how long, with which motion, text and music -- at the
``video_plan`` gate, in a closed vocabulary (``schemas.py``), and checks the
result at ``video_qa``. Everything lands in ``workspace/<series>/<chapter>/
video/`` and, once approved, in ``videos/<series>/<chapter>/`` -- never in
``library/``.
"""
