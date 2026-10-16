"""The ``video_plan`` vocabulary: what the agent may ask the renderer for.

Like ``visual_qa``, the gate takes a closed vocabulary. The agent decides what
the edit is; the renderer decides how pixels move. A response naming anything
outside these enums fails validation before a frame is drawn.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

#: Output frame, TikTok's 9:16.
WIDTH, HEIGHT, FPS = 1080, 1920, 30
#: The operator's length (2026-10-14): 25-40 seconds.
MIN_SECONDS, MAX_SECONDS = 25.0, 40.0
MIN_SHOTS = 4


class Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Motion(str, Enum):
    STATIC = "static"
    ZOOM_IN = "zoom_in"          # slow push in
    ZOOM_OUT = "zoom_out"        # slow pull back
    PAN_LEFT = "pan_left"        # the frame travels left across the art
    PAN_RIGHT = "pan_right"
    PAN_UP = "pan_up"
    PAN_DOWN = "pan_down"        # reveal a tall panel top to bottom
    PUNCH = "punch"              # a fast zoom that lands, then holds
    SHAKE = "shake"              # an impact shake that settles
    DRIFT = "drift"              # a slow, slight float


class Transition(str, Enum):
    CUT = "cut"
    FADE = "fade"                # crossfade
    DIP_BLACK = "dip_black"
    DIP_WHITE = "dip_white"
    FLASH = "flash"              # white flash into the new shot
    SLIDE_UP = "slide_up"
    SLIDE_LEFT = "slide_left"
    WHIP = "whip"                # fast blurred slide
    ZOOM_BLUR = "zoom_blur"      # zoom through, blurred
    GLITCH = "glitch"            # sliced, colour-split switch


class Effect(str, Enum):
    GRAYSCALE = "grayscale"
    DESATURATE = "desaturate"
    WARM = "warm"
    COOL = "cool"
    VIGNETTE = "vignette"
    GLOW = "glow"
    GRAIN = "grain"
    MIRROR_BEAT = "mirror_beat"  # flip horizontally on alternate beats
    FLASH_BEAT = "flash_beat"    # a light flash on every beat
    RGB_SPLIT = "rgb_split"      # colour fringes on the beat


class Fit(str, Enum):
    AUTO = "auto"                # cover when the crop is near 9:16, else contain
    COVER = "cover"              # fill the frame, cropping the excess
    CONTAIN = "contain"          # whole crop over a blurred copy of itself


class TextRole(str, Enum):
    HOOK = "hook"                # the opening line
    CAPTION = "caption"          # a short narrative line
    QUOTE = "quote"              # an original line of dialogue, quoted
    #: Small narration across a split screen's seam, CapCut BL-edit style
    #: (@muoivungvietnam): the plot so far plus a quoted line, 3-4 lines.
    NOTE = "note"


class TextPos(str, Enum):
    TOP = "top"
    CENTER = "center"
    BOTTOM = "bottom"


class TextAnim(str, Enum):
    FADE = "fade"
    POP = "pop"
    SLIDE_UP = "slide_up"
    TYPEWRITER = "typewriter"


class Crop(Base):
    """A rectangle in page pixels, inside the panel."""

    x1: int = Field(ge=0)
    y1: int = Field(ge=0)
    x2: int
    y2: int

    @model_validator(mode="after")
    def _ordered(self) -> "Crop":
        if self.x2 - self.x1 < 40 or self.y2 - self.y1 < 40:
            raise ValueError("crop must be at least 40x40 px")
        return self


class TextOverlay(Base):
    text: str = Field(min_length=1, max_length=200)
    role: TextRole = TextRole.CAPTION
    position: TextPos = TextPos.BOTTOM
    anim: TextAnim = TextAnim.FADE
    #: Seconds into the shot; ``end`` None = to the end of the shot.
    start: float = Field(default=0.0, ge=0)
    end: float | None = None


class Shot(Base):
    #: A panel id from the request, e.g. ``p013_k2``.
    panel: str = Field(pattern=r"^p\d{3}_k\d+$")
    #: Another chapter of the same series (e.g. "5"); default the plan's own.
    #: A video may draw its best shots from several chapters (the operator,
    #: 2026-10-14).
    chapter: str | None = None
    #: A second image stacked under the first -- the two-image split screen
    #: of CapCut BL templates (muoivungvietnam). Each half is 1080x960.
    panel2: str | None = Field(default=None, pattern=r"^p\d{3}_k\d+$")
    chapter2: str | None = None
    crop2: "Crop | None" = None
    motion2: Motion | None = None
    #: Tighter framing inside the panel (page pixels), e.g. on a face.
    crop: Crop | None = None
    seconds: float = Field(ge=0.3, le=8.0)
    motion: Motion = Motion.STATIC
    #: 0..1 -- how far the motion travels (zoom amount, pan distance, shake).
    strength: float = Field(default=0.5, ge=0, le=1)
    #: How this shot arrives from the previous one (ignored on the first).
    transition: Transition = Transition.CUT
    transition_seconds: float = Field(default=0.4, ge=0.05, le=1.5)
    effects: list[Effect] = Field(default_factory=list)
    fit: Fit = Fit.AUTO
    text: TextOverlay | None = None


class MusicChoice(Base):
    track: str
    #: Where in the track the video starts, in seconds.
    start: float = Field(default=0.0, ge=0)
    #: Move each cut to the nearest beat (within a quarter of the shot).
    beat_sync: bool = True


class VideoPlan(Base):
    """The agent's answer at ``video_plan``; also what ``render`` replays."""

    gate: str = "video_plan"
    style: str
    shots: list[Shot] = Field(min_length=MIN_SHOTS)
    music: MusicChoice | None = None
    #: The TikTok post text: one hook line in the channel's voice.
    caption: str = Field(min_length=1, max_length=300)
    hashtags: list[str] = Field(default_factory=list, max_length=12)
    #: Behind a contained (whole) panel: the page's white, black, or a blurred
    #: copy of the art. BL recap edits (jaeha_bl, alsvcra) set whole panels on
    #: white; the blurred backdrop read as an amateur slideshow.
    background: str = Field(default="white", pattern="^(white|black|blur)$")
    #: Effects applied to every shot on top of the shot's own.
    effects: list[Effect] = Field(default_factory=list)
    #: A short note on the story the sequence tells (kept with the plan).
    story: str = ""

    @field_validator("hashtags")
    @classmethod
    def _tags(cls, tags: list[str]) -> list[str]:
        out = []
        for t in tags:
            t = t.strip().lstrip("#")
            if not t or " " in t:
                raise ValueError(f"hashtag {t!r} must be one word")
            out.append(t)
        return out

    @property
    def seconds(self) -> float:
        return sum(s.seconds for s in self.shots)

    def check_length(self, low: float = MIN_SECONDS, high: float = MAX_SECONDS) -> None:
        if not low <= self.seconds <= high:
            raise ValueError(f"the shots add up to {self.seconds:.1f}s; the target is {low:.0f}-{high:.0f}s")


class QaResponse(Base):
    gate: str = "video_qa"
    passed: bool
    #: What was wrong, when not passed (the fix is a revised plan).
    notes: list[str] = Field(default_factory=list)
