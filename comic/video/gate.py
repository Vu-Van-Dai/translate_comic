"""The two video gates and the files between them.

``video_plan``: the pipeline cuts the pages into panels, draws numbered
sheets, and writes a request carrying everything the agent needs to choose:
the chapter's story (summary and every translated line, by page), the
styles, the music, the series' rules. The agent answers with a ``VideoPlan``.

``video_qa``: after rendering, a sheet of frames (every shot's start, middle
and end, every caption) and the files themselves. The agent answers
``passed``; a fix is a revised plan and another render, at most
``MAX_ROUNDS`` times.

Workspace layout, all under ``workspace/<series>/<chapter>/video/``::

    panels.json               detected panels per page, and the art used
    handoff/                  requests, responses, sheets
    plan.json                 the accepted plan (what was rendered)
    out/                      video.mp4, video_silent.mp4, cover.png, caption.txt
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw
from pydantic import ValidationError

from comic.paths import ChapterPaths, SeriesPaths, project_root
from comic.video import music as music_lib
from comic.video.panels import chapter_panels, draw_sheets, find_panel, save_panels
from comic.video.schemas import FPS, HEIGHT, WIDTH, MAX_SECONDS, MIN_SECONDS, QaResponse, TextRole, VideoPlan
from comic.video.source import ArtSource, default_source
from comic.video.styles import series_video, styles

MAX_ROUNDS = 3
#: Past this a full-frame shot of a ~730 px scan turns visibly soft
#: (Super Lovers ch.2: p.34 at 7.7x was mush, p.17 at 5.5x still read fine).
MAX_UPSCALE = 6.0
GATE_EXIT = 10

SELECTION_RULES = """\
Choose 4-5 or more panels (usually 7-12) that tell a short, coherent story of
this chapter, judged on: visual appeal and how engaging each image is; the
scene's importance to the chapter; character expressions and noteworthy
moments; and whether the sequence reads as a story on its own (hook ->
setup -> turn -> ending).

Images are the source pages as served (any language) or text-free art --
never our Vietnamese translation (the renderer refuses those files). Crop away empty speech
balloons where you can (a `crop` in page pixels; the sheets carry a 100 px
grid). The hook belongs in the first 3 seconds; end on a natural beat or a
cliffhanger. No branding. Background music only -- no narration.

On-screen text is optional: a hook line, short captions, and occasionally a
quoted line of the original dialogue (role "quote", in Vietnamese). Keep each
line short; the renderer keeps text inside TikTok's safe area.

Hard rules: never select nudity or sexualised framing of a character who is a
minor in this chapter, and never pair a minor with a romantic hook, romantic
music or a couple-style edit (see `series.minors`, `series.forbid_styles`).
"""


@dataclass
class VideoPaths:
    chapter: ChapterPaths
    #: A named video (an arc recap, a showcase) hosted in this chapter: its
    #: plan, gates and output live in ``video/<name>/`` and it publishes to
    #: ``videos/<series>/<name>/``, so one chapter can host several videos.
    name: str | None = None

    @property
    def root(self) -> Path:
        base = self.chapter.workspace / "video"
        return base / self.name if self.name else base

    @property
    def panels(self) -> Path:
        # Panels belong to the chapter's pages, shared by every video.
        return self.chapter.workspace / "video" / "panels.json"

    @property
    def handoff(self) -> Path:
        return self.root / "handoff"

    def request(self, gate: str) -> Path:
        return self.handoff / f"{gate}_request.json"

    def response(self, gate: str) -> Path:
        return self.handoff / f"{gate}_response.json"

    @property
    def plan(self) -> Path:
        return self.root / "plan.json"

    @property
    def state(self) -> Path:
        return self.root / "state.json"

    @property
    def out(self) -> Path:
        return self.root / "out"

    @property
    def final(self) -> Path:
        c = self.chapter
        return c.root / "videos" / c.series / (self.name or c.chapter)


def _write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def _state(vp: VideoPaths) -> dict:
    return json.loads(vp.state.read_text(encoding="utf-8")) if vp.state.exists() else {"round": 0}


def _story(paths: ChapterPaths) -> dict:
    """Every translated line by page, and the chapter summary, for context."""
    by_page: dict[str, list[dict]] = {}
    if paths.translation_file.exists():
        data = json.loads(paths.translation_file.read_text(encoding="utf-8"))
        for e in data.get("entries", []):
            page = str(int(e["region_id"][1:4]))
            by_page.setdefault(page, []).append(
                {"id": e["region_id"], "vi": e["target_text"], "en": e.get("source_text", ""),
                 "speaker": e.get("speaker")})
    summary = ""
    sums = SeriesPaths(paths.series).profile_dir / "memory" / "chapter_summaries.json"
    if sums.exists():
        for s in json.loads(sums.read_text(encoding="utf-8")):
            if s.get("chapter") == paths.chapter:
                summary = s.get("summary", "")
    return {"summary": summary, "lines_by_page": by_page}


def open_plan(paths: ChapterPaths, prefer: ArtSource | None = None, name: str | None = None) -> Path:
    """Detect panels, draw the sheets, write the ``video_plan`` request."""
    vp = VideoPaths(paths, name)
    prefer = prefer or default_source(paths.series)
    rules = series_video(paths.series)
    state = json.loads(paths.state_file.read_text(encoding="utf-8")) if paths.state_file.exists() else {}
    pages = [p["page"] for p in state.get("pages", []) if not p.get("excluded")] or sorted(
        int(f.stem) for f in paths.original.glob("[0-9][0-9][0-9].*"))
    payload = chapter_panels(paths, pages, prefer, rtl=rules.rtl)
    save_panels(payload, vp.panels)
    sheet_dir = vp.handoff / "video_plan"
    if sheet_dir.exists():
        shutil.rmtree(sheet_dir)
    sheets = draw_sheets(payload, sheet_dir)
    lib = music_lib.library()
    request = {
        "gate": "video_plan",
        "series": paths.series,
        "chapter": paths.chapter,
        "instructions": SELECTION_RULES,
        "seconds": [MIN_SECONDS, MAX_SECONDS],
        "sheets": [str(s) for s in sheets],
        "panels": {page: [p["id"] for p in info["panels"]] for page, info in payload["pages"].items()},
        "art": {page: info["kind"] for page, info in payload["pages"].items()},
        "story": _story(paths),
        "series_rules": {"minors": rules.minors, "forbid_styles": rules.forbid_styles,
                         "hashtags": rules.hashtags, "notes": rules.notes},
        "styles": {n: {"about": s.about, "shot_seconds": s.shot_seconds, "music_moods": s.music,
                       "adults_only": s.adults_only} for n, s in styles().items()},
        "music": [{"id": t.id, "title": t.title, "moods": t.moods,
                   **{k: v for k, v in music_lib.analyse(t).items() if k in ("duration", "bpm")}}
                  for t in lib.values() if t.path.exists()],
        "schema": "comic/video/schemas.py: VideoPlan",
        "write": str(vp.response("video_plan")),
    }
    _write(vp.request("video_plan"), request)
    _write(vp.state, {**_state(vp), "status": "awaiting_plan"})
    return vp.request("video_plan")


class PanelIndex:
    """Panels of the plan's chapter -- and of any other chapter a shot names.

    Another chapter's page is cut into panels on first use, from its
    original pages (the default source), and remembered in that chapter's
    own ``video/panels.json``.
    """

    def __init__(self, paths: ChapterPaths):
        self.paths = paths
        self.cache: dict[str, dict] = {}

    def _payload(self, chapter: str | None) -> tuple[ChapterPaths, dict]:
        cp = self.paths if chapter is None else ChapterPaths(self.paths.series, chapter, root=self.paths.root)
        key = cp.chapter
        if key not in self.cache:
            f = VideoPaths(cp).panels
            self.cache[key] = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {
                "prefer": default_source(cp.series).value, "pages": {}}
        return cp, self.cache[key]

    def find(self, panel_id: str, chapter: str | None = None) -> tuple[dict, dict]:
        cp, payload = self._payload(chapter)
        page = str(int(panel_id[1:4]))
        if page not in payload["pages"]:
            fresh = chapter_panels(cp, [int(page)], ArtSource(payload.get("prefer", "original")),
                                   rtl=series_video(cp.series).rtl)
            if page not in fresh["pages"]:
                raise KeyError(f"{panel_id}: {cp.chapter} has no page {page}")
            payload["pages"][page] = fresh["pages"][page]
            save_panels(payload, VideoPaths(cp).panels)
        return find_panel(payload, panel_id)


def load_plan(paths: ChapterPaths, name: str | None = None) -> tuple[VideoPlan, list[str]]:
    """The agent's plan, validated against the panels, styles, music and rules."""
    vp = VideoPaths(paths, name)
    raw = vp.response("video_plan")
    if not raw.exists():
        return None, [f"no plan at {raw}"]
    try:
        plan = VideoPlan.model_validate_json(raw.read_text(encoding="utf-8"))
    except ValidationError as exc:
        return None, [f"plan does not match the schema:\n{exc}"]
    problems = []
    index = PanelIndex(paths)
    for i, shot in enumerate(plan.shots, 1):
        try:
            _info, panel = index.find(shot.panel, shot.chapter)
            if shot.panel2:
                index.find(shot.panel2, shot.chapter2 or shot.chapter)
        except KeyError as exc:
            problems.append(f"shot {i}: {exc}")
            continue
        if shot.crop:
            c = shot.crop
            if c.x2 <= panel["x1"] or c.x1 >= panel["x2"] or c.y2 <= panel["y1"] or c.y1 >= panel["y2"]:
                problems.append(f"shot {i}: crop lies outside {shot.panel}")
        if shot.fit.value == "cover":
            c = shot.crop
            w = (min(c.x2, panel["x2"]) - max(c.x1, panel["x1"])) if c else panel["x2"] - panel["x1"]
            h = (min(c.y2, panel["y2"]) - max(c.y1, panel["y1"])) if c else panel["y2"] - panel["y1"]
            # The frame's width comes from min(w, h * 9/16) source pixels.
            if 1080 / min(w, h * 9 / 16) > MAX_UPSCALE:
                problems.append(f"shot {i}: fit=cover blows {shot.panel} up "
                                f"{1080 / min(w, h * 9 / 16):.1f}x -- widen the crop or use contain")
        if shot.text and shot.text.end is not None and shot.text.end <= shot.text.start:
            problems.append(f"shot {i}: text ends before it starts")
    all_styles = styles()
    rules = series_video(paths.series)
    if plan.style not in all_styles:
        problems.append(f"unknown style {plan.style!r} ({', '.join(all_styles)})")
    elif plan.style in rules.forbid_styles:
        problems.append(f"style {plan.style!r} is forbidden for this series (video.yaml)")
    if plan.music and plan.music.track not in music_lib.library():
        problems.append(f"unknown music track {plan.music.track!r}")
    try:
        plan.check_length()
    except ValueError as exc:
        problems.append(str(exc))
    return plan, problems


def apply_style(plan: VideoPlan, style) -> VideoPlan:
    """Fill what each shot left unsaid from the style's defaults.

    Only fields the agent did not write are filled (pydantic's
    ``model_fields_set``), so an explicit ``"motion": "static"`` stays static.
    The style's effects always apply, on top of the plan's and the shot's.
    """
    from comic.video.schemas import Effect, Motion, TextAnim, Transition

    shots = []
    for i, shot in enumerate(plan.shots):
        given = shot.model_fields_set
        upd = {}
        if "motion" not in given:
            upd["motion"] = Motion(style.motions[i % len(style.motions)])
        if "strength" not in given:
            upd["strength"] = style.strength
        if "transition" not in given:
            upd["transition"] = Transition(style.transition)
        if "transition_seconds" not in given:
            upd["transition_seconds"] = style.transition_seconds
        upd["effects"] = list(dict.fromkeys([Effect(e) for e in style.effects] + list(shot.effects)))
        if shot.text is not None and "anim" not in shot.text.model_fields_set and style.text.get("anim"):
            upd["text"] = shot.text.model_copy(update={"anim": TextAnim(style.text["anim"])})
        shots.append(shot.model_copy(update=upd))
    return plan.model_copy(update={"shots": shots})


def render(paths: ChapterPaths, plan: VideoPlan, log=print, name: str | None = None) -> dict:
    """Render the plan: both MP4s, cover, caption, music cue, QA sheet."""
    from comic.video.render import Timeline, encode, mux, prepare

    vp = VideoPaths(paths, name)
    index = PanelIndex(paths)
    style = styles()[plan.style]
    plan = apply_style(plan, style)
    lib = music_lib.library()
    track = lib.get(plan.music.track) if plan.music else None
    durations = [s.seconds for s in plan.shots]
    beats_video: list[float] = []
    bpm = None
    if track:
        info = music_lib.analyse(track)
        bpm = info["bpm"]
        if plan.music.beat_sync:
            durations = music_lib.snap_cuts(durations, info["beats"], plan.music.start)
        beats_video = [round(b - plan.music.start, 3) for b in info["beats"]
                       if plan.music.start <= b <= plan.music.start + sum(durations) + 1]

    fonts = style.text
    prepared, t = [], 0.0
    for i, (shot, secs) in enumerate(zip(plan.shots, durations)):
        info, panel = index.find(shot.panel, shot.chapter)
        font = fonts.get("hook_font" if shot.text and shot.text.role is TextRole.HOOK else "caption_font",
                         "BeVietnamPro-Bold.ttf")
        box = (panel["x1"], panel["y1"], panel["x2"], panel["y2"])
        if shot.panel2:
            half = (WIDTH, HEIGHT // 2)
            top = prepare(shot, Path(info["source"]), box, t, secs, plan.effects, font, i, plan.background, half)
            info2, panel2 = index.find(shot.panel2, shot.chapter2 or shot.chapter)
            lower = shot.model_copy(update={"crop": shot.crop2, "motion": shot.motion2 or shot.motion, "text": None})
            top.partner = prepare(lower, Path(info2["source"]), (panel2["x1"], panel2["y1"], panel2["x2"], panel2["y2"]),
                                  t, secs, plan.effects, font, i + 500, plan.background, half)
            prepared.append(top)
        else:
            prepared.append(prepare(shot, Path(info["source"]), box, t, secs, plan.effects, font, i, plan.background))
        t += secs
    timeline = Timeline(prepared, beats_video)
    total = timeline.seconds

    out = vp.out
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    samples = _sample_times(prepared)
    thumbs: dict[float, Image.Image] = {}
    cover_at = min(prepared[0].seconds * 0.6, 1.8)
    cover_frame = round(cover_at * FPS)

    def on_frame(n, t, im):
        if n == cover_frame:
            im.save(out / "cover.png")
        for s in samples:
            if n == round(s * FPS):
                thumbs[s] = im.resize((270, 480), Image.BILINEAR)
        if n % (FPS * 5) == 0:
            log(f"  {t:5.1f}s / {total:.1f}s")

    picture = encode(timeline, out / "picture.mp4", on_frame)
    if track:
        audio = music_lib.prepare_audio(track, plan.music.start, total, out / "music.wav")
        mux(picture, audio, out / "video.mp4", total)
        audio.unlink(missing_ok=True)
    mux(picture, None, out / "video_silent.mp4", total)
    picture.unlink(missing_ok=True)

    tags = " ".join("#" + h for h in plan.hashtags)
    credit = f"\n\nNhạc: {track.credit}" if track and track.credit else ""
    (out / "caption.txt").write_text(f"{plan.caption}\n{tags}{credit}\n", encoding="utf-8")
    cuts, acc = [], 0.0
    for d in durations[:-1]:
        acc += d
        cuts.append(round(acc, 2))
    cue = [f"Length: {total:.2f}s", f"Cuts at (s): {', '.join(str(c) for c in cuts)}"]
    if track:
        cue += [f"Music in video.mp4: {track.title} -- {track.artist}, from {plan.music.start:.1f}s ({bpm} BPM)",
                "For video_silent.mp4: add a TikTok sound in the app; the cuts above follow "
                f"{'the beat of the track above' if plan.music.beat_sync else 'the plan, not a beat'}."]
    (out / "music_cue.txt").write_text("\n".join(cue) + "\n", encoding="utf-8")

    sheet = _qa_sheet(thumbs, out.parent / "handoff" / "video_qa_frames.png")
    _write(vp.plan, json.loads(plan.model_dump_json()) | {"rendered_seconds": durations})
    st = _state(vp)
    st.update(status="awaiting_qa", round=st.get("round", 0) + 1)
    _write(vp.state, st)
    _write(vp.request("video_qa"), {
        "gate": "video_qa",
        "round": st["round"],
        "max_rounds": MAX_ROUNDS,
        "frames": str(sheet),
        "video": str(out / "video.mp4" if track else out / "video_silent.mp4"),
        "checklist": [
            "every caption readable and inside the frame, not over a face",
            "no translated lettering anywhere (text-free art only)",
            "no shot dominated by an empty balloon; crops do not cut faces",
            "the sequence tells the story; the hook lands in the first 3 s",
            "minors never framed romantically; no nudity of a minor",
        ],
        "write": str(vp.response("video_qa")),
        "fix": "revise video_plan_response.json and run `comic video render` again",
    })
    return {"seconds": total, "durations": durations, "out": out, "sheet": sheet, "round": st["round"]}


def _sample_times(prepared) -> list[float]:
    times = []
    for p in prepared:
        lead = min(p.shot.transition_seconds, p.seconds * 0.6) if p.start else 0
        for t in (p.start + lead + 0.05, p.start + p.seconds / 2, p.start + p.seconds - 0.08):
            times.append(round(t, 3))
    return sorted(set(times))


def _qa_sheet(thumbs: dict[float, Image.Image], path: Path) -> Path:
    keys = sorted(thumbs)
    cols = 9
    rows = max(1, (len(keys) + cols - 1) // cols)
    sheet = Image.new("RGB", (cols * 274, rows * 504), (50, 50, 50))
    d = ImageDraw.Draw(sheet)
    for i, k in enumerate(keys):
        x, y = (i % cols) * 274, (i // cols) * 504
        sheet.paste(thumbs[k], (x + 2, y + 22))
        d.text((x + 4, y + 4), f"{k:.2f}s", fill=(255, 255, 0))
    path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(path)
    return path


def publish(paths: ChapterPaths, force: bool = False, name: str | None = None) -> Path:
    """Copy the approved outputs to ``videos/<series>/<chapter or name>/``.

    ``name`` selects a named video hosted in this chapter (see
    ``VideoPaths.name``); the plan goes along as ``plan.json``.
    """
    vp = VideoPaths(paths, name)
    resp = vp.response("video_qa")
    if not force:
        if not resp.exists():
            raise ValueError(f"no QA answer at {resp}")
        qa = QaResponse.model_validate_json(resp.read_text(encoding="utf-8"))
        if not qa.passed:
            raise ValueError("QA did not pass: " + "; ".join(qa.notes))
    final = vp.final
    final.mkdir(parents=True, exist_ok=True)
    for f in final.iterdir():
        f.unlink()
    for f in vp.out.iterdir():
        shutil.copy2(f, final / f.name)
    if vp.response("video_plan").exists():
        shutil.copy2(vp.response("video_plan"), final / "plan.json")
    st = _state(vp)
    st["status"] = "published"
    _write(vp.state, st)
    return final
