"""Pure, OS-independent rendering for the ADR-0010 visible indicator.

Kept as pixel-in/pixel-out functions so the look (``Theme``) can be replaced without touching the
Win32 host (``win32_overlay.py``) or the state machine that drives it (``automation_indicator.py``).
No text is drawn (ADR-0010: language independence). The corner orb is Finitact; the small spark
beside it is the external decision provider, lit only while a request to it is in flight
(ADR-0010 追記4). The reticle over each input target is a small orb in the same look.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, replace

import numpy as np
from PIL import Image, ImageChops

Rect = tuple[int, int, int, int]  # left, top, width, height
RGB = tuple[int, int, int]


@dataclass(frozen=True)
class RenderedImage:
    width: int
    height: int
    bgra_premultiplied: bytes


@dataclass(frozen=True)
class Theme:
    """Straight (non-premultiplied) RGB colors; premultiplication happens once, in `_to_rendered`."""

    orb_size: int = 96
    # Indigo base, tinted per phase (ADR-0010 追記2). ``orb_rim`` keys: idle, observe, think, act.
    orb_core: RGB = (178, 196, 255)
    orb_rim: tuple[tuple[str, RGB], ...] = (
        ("idle", (38, 48, 150)),
        ("observe", (26, 74, 176)),
        ("think", (68, 40, 160)),
        ("act", (40, 76, 200)),
    )
    # Amber keeps the provider visibly apart from Finitact's indigo.
    provider_color: RGB = (255, 178, 72)
    # None follows the orb's ``act`` rim, so the reticle and the orb read as one thing.
    reticle_color: RGB | None = None
    animated: bool = True


DEFAULT_THEME = Theme()

INDICATOR_ENV_PREFIX = "FINITACT_INDICATOR_"
ORB_SIZE_LIMITS = (48, 192)


def theme_from_env(environ: Mapping[str, str]) -> Theme:
    """``DEFAULT_THEME`` with the ``FINITACT_INDICATOR_*`` overrides applied; a malformed value raises."""

    theme = DEFAULT_THEME
    size = _env_value(environ, "SIZE")
    if size is not None:
        low, high = ORB_SIZE_LIMITS
        if not size.isdigit() or not low <= int(size) <= high:
            raise ValueError(f"{INDICATOR_ENV_PREFIX}SIZE must be an integer from {low} to {high} px, got {size!r}")
        theme = replace(theme, orb_size=int(size))
    animation = _env_value(environ, "ANIMATION")
    if animation is not None:
        if animation not in ("0", "1"):
            raise ValueError(f"{INDICATOR_ENV_PREFIX}ANIMATION must be 0 or 1, got {animation!r}")
        theme = replace(theme, animated=animation == "1")
    core = _env_color(environ, "COLOR_CORE")
    if core is not None:
        theme = replace(theme, orb_core=core)
    rim = dict(theme.orb_rim)
    for key in rim:
        color = _env_color(environ, f"COLOR_{key.upper()}")
        if color is not None:
            rim[key] = color
    theme = replace(theme, orb_rim=tuple(rim.items()))
    provider = _env_color(environ, "COLOR_PROVIDER")
    if provider is not None:
        theme = replace(theme, provider_color=provider)
    reticle = _env_color(environ, "COLOR_RETICLE")
    if reticle is not None:
        theme = replace(theme, reticle_color=reticle)
    return theme


def _env_value(environ: Mapping[str, str], name: str) -> str | None:
    raw = environ.get(INDICATOR_ENV_PREFIX + name, "").strip()
    return raw or None


def _env_color(environ: Mapping[str, str], name: str) -> RGB | None:
    raw = _env_value(environ, name)
    if raw is None:
        return None
    digits = raw.removeprefix("#")
    if len(digits) != 6 or any(char not in "0123456789abcdefABCDEF" for char in digits):
        raise ValueError(f"{INDICATOR_ENV_PREFIX}{name} must be a #RRGGBB color, got {raw!r}")
    return int(digits[0:2], 16), int(digits[2:4], 16), int(digits[4:6], 16)


def corner_position(
    corner: str, screen_width: int, screen_height: int, width: int, height: int, margin: int
) -> tuple[int, int]:
    x = screen_width - width - margin if "right" in corner else margin
    y = screen_height - height - margin if "bottom" in corner else margin
    return x, y


@dataclass(frozen=True)
class ProviderState:
    """Whether a provider request is in flight, and for how long that has been true or false."""

    active: bool = False
    seconds: float = 1e9


ORB_FPS = 20
# A still frame stands for a phase when animation is off; t at which every effect has eased in.
STILL_SECONDS = 1.0
_TAU = 2 * math.pi
_BEATING = ("click", "double_click", "right_click", "middle_click", "ctrl_click", "shift_click")
_BEAT_SECONDS = 1.1
_EASE_SECONDS = 0.5
# Lobes that drift around the center and merge with it, so the outline never settles into a circle.
# (orbit radius / R, lobe radius / R, angular speed rad/s, phase offset)
_ORB_LOBES = ((0.42, 0.62, 0.31, 0.0), (0.38, 0.55, -0.23, 2.3), (0.30, 0.50, 0.17, 4.1))
# Orb and spark positions as fractions of the canvas: the spark sits apart, up and to the left.
_ORB_CENTER = 0.57
_SPARK_CENTER = 0.18
_SEND_SECONDS = 0.3
# 2 is an ellipse, larger tends to a box; 2.6 hugs a wide input field without reading as a rectangle.
# The reticle orb marks roughly where input lands; translucent so the target stays readable through it.
_RETICLE_OPACITY = 0.75
_RETICLE_SIZE_LIMITS = (44, 96)
_RETURN_SECONDS = 0.45


def render_orb(
    theme: Theme,
    phase: str,
    t: float,
    provider: ProviderState = ProviderState(),
    *,
    solo: bool = False,
    heading: tuple[float, float] | None = None,
) -> RenderedImage:
    """One frame of the corner orb at ``t`` seconds into ``phase``.

    A soft-edged glow made of a core and drifting lobes whose outline wobbles like something
    alive. Motion and a shift of tint tell phases apart. Effects ease in over ``_EASE_SECONDS`` so
    a change never jumps, and every pulse stays below 3 Hz because the orb stays up all run.
    """

    ease = _ease(t)
    size = theme.orb_size
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32) + 0.5
    # A solo orb (the reticle) has no provider spark beside it, so it takes the canvas center.
    unit = size * (0.2 if solo else 0.17)
    center = 0.5 if solo else _ORB_CENTER

    breath = 0.05 * math.sin(_TAU * 0.28 * t)
    glow = 0.82 + 0.18 * math.sin(_TAU * 0.28 * t + 0.6)
    cx = size * center + 0.05 * unit * math.sin(_TAU * 0.13 * t)
    cy = size * center + 0.04 * unit * math.sin(_TAU * 0.17 * t + 1.1)
    radius, stretch_x, stretch_y, ripple, spot = unit * (1 + breath), 1.0, 1.0, 0.0, None
    membrane = [(2, 0.07, 0.5, 0.0), (3, 0.05, -0.7, 1.7)]
    lobe_spread = 1.0
    # Extra blobs merged into the body: (x, y, radius, strength).
    extras: list[tuple[float, float, float, float]] = []
    beat = t % _BEAT_SECONDS
    if phase == "observe":
        angle = _TAU * 0.45 * t
        spot = (cx + 0.45 * radius * math.cos(angle), cy + 0.45 * radius * math.sin(angle))
    elif phase == "think":
        ripple = ease * 0.45
        glow += ease * 0.12 * math.sin(_TAU * 0.9 * t)
        lobe_spread = 1 - 0.35 * ease
    elif phase in _BEATING:
        pulse = _bump(beat, 0.12) + (0.8 * _bump(beat, 0.34) if phase == "double_click" else 0.0)
        radius *= 1 - ease * 0.10 * pulse
        glow += ease * 0.25 * pulse
        if phase == "right_click":
            reach = ease * _bump(beat, 0.36, 0.012)
            extras.append((cx + 0.8 * radius * reach, cy + 0.8 * radius * reach, 0.45 * radius, 0.9 * reach))
        elif phase == "middle_click":
            stretch_y = 1 + ease * 0.2 * pulse
        elif phase in ("ctrl_click", "shift_click"):
            # The modifier's companion: ctrl circles clockwise, shift the other way.
            angle = _TAU * 0.35 * t * (1 if phase == "ctrl_click" else -1)
            extras.append((cx + 1.25 * radius * math.cos(angle), cy + 1.25 * radius * math.sin(angle),
                           0.24 * radius, ease * 0.9))
    elif phase == "key":
        squeeze, flash = _bump(beat, 0.10), _bump(beat, 0.24, 0.002)
        radius *= 1 - ease * 0.16 * squeeze
        glow += ease * 0.45 * flash
    elif phase == "hover":
        cy -= ease * 0.18 * unit * math.sin(_TAU * 0.35 * t)
        cx += ease * 0.10 * unit * math.sin(_TAU * 0.22 * t + 0.7)
    elif phase in ("type", "fill"):
        membrane += [(6, ease * 0.04, 5.0, 0.0), (7, ease * 0.03, -6.0, 1.0)]
    elif phase == "scroll":
        spot = (cx, cy + 0.5 * radius * math.sin(_TAU * 0.8 * t))
    elif phase in ("scroll_up", "scroll_down"):
        flow = (t * 0.9) % 1.0
        sign = -1 if phase == "scroll_up" else 1
        spot = (cx, cy + sign * radius * (1.2 * flow - 0.6))
    elif phase == "select":
        cy -= ease * 0.22 * unit * max(0.0, math.sin(_TAU * 0.8 * t))
    elif phase == "drag":
        hx, hy = heading or (1.0, 0.0)
        norm = math.hypot(hx, hy) or 1.0
        hx, hy = hx / norm, hy / norm
        extend = ease * (0.7 + 0.3 * math.sin(_TAU * 0.6 * t))
        for step, strength in ((0.6, 0.8), (1.15, 0.55), (1.65, 0.35)):
            distance_out = step * radius * extend
            extras.append((cx + hx * distance_out, cy + hy * distance_out, 0.5 * radius, strength * extend))
    elif phase == "set_range":
        stretch_x = 1 + ease * 0.25 * math.sin(_TAU * 0.7 * t)

    # An answer arriving from the provider brightens the orb as it lands.
    if not provider.active and provider.seconds < _RETURN_SECONDS + 0.4:
        landed = provider.seconds - _RETURN_SECONDS
        glow += 0.3 * math.exp(-(landed**2) / 0.02)

    dx, dy = (xx - cx) / stretch_x, (yy - cy) / stretch_y
    distance = np.hypot(dx, dy)
    theta = np.arctan2(dy, dx)
    edge = np.full_like(distance, radius)
    for k, amplitude, speed, offset in membrane:
        edge *= 1 + amplitude * np.sin(k * theta + speed * t + offset)
    field = np.exp(-((distance / edge) ** 2) * 1.5)
    for orbit, lobe, speed, offset in _ORB_LOBES:
        angle = speed * t + offset
        wobble = 1 + 0.25 * math.sin(_TAU * 0.11 * t + offset)
        lx = cx + lobe_spread * orbit * radius * wobble * math.cos(angle) * stretch_x
        ly = cy + lobe_spread * orbit * radius * wobble * math.sin(angle)
        field = field + 0.55 * np.exp(-(((xx - lx) ** 2 + (yy - ly) ** 2) / (lobe * radius) ** 2) * 1.5)
    for ex, ey, extra_radius, strength in extras:
        field = field + strength * np.exp(-(((xx - ex) ** 2 + (yy - ey) ** 2) / extra_radius**2) * 1.5)
    halo = 1 - np.exp(-1.6 * field)
    # The core follows the membrane only weakly, so it stays round while the edge wobbles.
    core = np.exp(-((distance / (radius + 0.3 * (edge - radius))) ** 2) * 5.0)
    if ripple:
        front = (t * 0.7) % 1.0
        core = core + ripple * (1 - front) * np.exp(-((distance / radius - 0.3 - front * 1.3) ** 2) / 0.02)
    if spot is not None:
        core = core + ease * 0.55 * _blob(xx, yy, spot, 0.32 * radius)

    alpha = np.clip(1.1 * halo * glow + 0.3 * core, 0.0, 1.0)
    mix = np.clip(0.85 * core * glow, 0.0, 1.0)[..., None]
    rgb = _orb_rim(theme, phase, ease) * (1 - mix) + _rgb(theme.orb_core) * mix

    if solo:
        return _pack(rgb, alpha * _RETICLE_OPACITY)
    spark_alpha, spark_rgb = _provider_layer(theme, provider, xx, yy, (cx, cy), unit, t)
    rgb = rgb * (1 - spark_alpha[..., None]) + spark_rgb * spark_alpha[..., None]
    alpha = np.maximum(alpha, spark_alpha)
    return _pack(rgb, alpha)


def _provider_layer(theme, provider: ProviderState, xx, yy, orb, unit, t):
    """The external provider: a dim spark apart from the orb, and the light that travels between them."""

    size = theme.orb_size
    spark = (size * _SPARK_CENTER, size * _SPARK_CENTER)
    s = provider.seconds
    if provider.active:
        brightness = 0.25 + 0.75 * min(1.0, s / _SEND_SECONDS) * (0.85 + 0.15 * math.sin(_TAU * 1.6 * s))
        travel = (orb, spark, s / _SEND_SECONDS) if s < _SEND_SECONDS else None
    else:
        brightness = 0.25 + 0.75 * max(0.0, 1 - s / _RETURN_SECONDS)
        travel = (spark, orb, s / _RETURN_SECONDS) if s < _RETURN_SECONDS else None
    twinkle = 0.85 + 0.15 * math.sin(_TAU * 0.37 * t + 0.8)
    radius = unit * (0.32 + 0.3 * brightness)
    alpha = brightness * twinkle * (0.7 * _blob(xx, yy, spark, radius * 2.0) + 0.9 * _blob(xx, yy, spark, radius))
    if travel is not None:
        start, end, progress = travel
        progress = progress * progress * (3 - 2 * progress)
        point = (start[0] + (end[0] - start[0]) * progress, start[1] + (end[1] - start[1]) * progress)
        alpha = alpha + 0.95 * _blob(xx, yy, point, unit * 0.3)
    alpha = np.clip(alpha, 0.0, 1.0)
    white = _blob(xx, yy, spark, radius * 0.6) * brightness
    rgb = _rgb(theme.provider_color) * (1 - white[..., None] * 0.45) + 255 * white[..., None] * 0.45
    return alpha, rgb


def _orb_rim(theme: Theme, phase: str, ease: float) -> np.ndarray:
    rims = dict(theme.orb_rim)
    key = phase if phase in ("idle", "observe", "think") else "act"
    base = _rgb(rims["idle"])
    return base + (_rgb(rims[key]) - base) * ease


def reticle_canvas_size(theme: Theme, rect: Rect) -> tuple[int, int]:
    _, _, width, height = rect
    low, high = _RETICLE_SIZE_LIMITS
    side = int(min(max(min(width, height) * 1.3 + 24, low), high))
    return side, side


def reticle_origin(theme: Theme, rect: Rect) -> tuple[int, int]:
    """Top-left of the reticle canvas in the same coordinate space as ``rect``: centered on the target."""

    left, top, width, height = rect
    side, _ = reticle_canvas_size(theme, rect)
    return left + width // 2 - side // 2, top + height // 2 - side // 2


def render_reticle(
    theme: Theme, rect: Rect, *, action: str, t: float, heading: tuple[float, float] | None = None
) -> RenderedImage:
    """One frame of the small orb over an input target, ``t`` seconds after it appeared.

    It marks roughly where input lands, in the corner orb's look, and moves the way ``render_orb``
    moves for that operation (ADR-0010 追記6); ``heading`` points a drag toward its end. Its tint
    follows the orb's ``act`` color unless ``reticle_color`` overrides it.
    """

    side, _ = reticle_canvas_size(theme, rect)
    rims = dict(theme.orb_rim)
    if theme.reticle_color is not None:
        rims["act"] = theme.reticle_color
    look = replace(theme, orb_size=side, orb_rim=tuple(rims.items()))
    # It appears from nothing: a first frame already drawn at full strength would pop.
    fade = _ease(max(0.0, t) * 1.5)
    frame = render_orb(look, action, t, solo=True, heading=heading)
    if fade >= 1.0:
        return frame
    pixels = np.frombuffer(frame.bgra_premultiplied, np.uint8).astype(np.float32) * fade
    return RenderedImage(frame.width, frame.height, pixels.round().astype(np.uint8).tobytes())


def _ease(t: float) -> float:
    ease = min(1.0, max(0.0, t) / _EASE_SECONDS)
    return ease * ease * (3 - 2 * ease)


def _bump(beat: float, at: float, width: float = 0.003) -> float:
    return math.exp(-((beat - at) ** 2) / width)


def _blob(xx, yy, center: tuple[float, float], radius: float) -> np.ndarray:
    return np.exp(-(((xx - center[0]) ** 2 + (yy - center[1]) ** 2) / radius**2))


def _rgb(color: RGB) -> np.ndarray:
    return np.array(color, dtype=np.float32)


def _pack(rgb: np.ndarray, alpha: np.ndarray) -> RenderedImage:
    rgba = np.dstack([rgb, alpha * 255]).round().clip(0, 255).astype(np.uint8)
    return _to_rendered(Image.fromarray(rgba, "RGBA"))


def _to_rendered(image: Image.Image) -> RenderedImage:
    r, g, b, a = image.convert("RGBA").split()
    premultiplied = Image.merge(
        "RGBA", (ImageChops.multiply(b, a), ImageChops.multiply(g, a), ImageChops.multiply(r, a), a)
    )
    return RenderedImage(image.width, image.height, premultiplied.tobytes())
