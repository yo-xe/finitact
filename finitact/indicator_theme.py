"""Pure, OS-independent rendering for the ADR-0010 visible indicator.

Kept as pixel-in/pixel-out functions so the look (``Theme``) can be replaced without touching the
Win32 host (``win32_overlay.py``) or the state machine that drives it (``automation_indicator.py``).
No text is drawn by the default theme (ADR-0010: language independence); a theme may add a label
as long as it stays optional.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from PIL import Image, ImageChops, ImageDraw, ImageFilter

Rect = tuple[int, int, int, int]  # left, top, width, height


@dataclass(frozen=True)
class RenderedImage:
    width: int
    height: int
    bgra_premultiplied: bytes


@dataclass(frozen=True)
class Theme:
    """Colors are straight (non-premultiplied) RGBA; premultiplication happens once, in `_to_rendered`."""

    badge_background: tuple[int, int, int, int] = (35, 25, 52, 224)
    badge_dot_idle: tuple[int, int, int, int] = (139, 107, 255, 255)
    badge_dot_delivering: tuple[int, int, int, int] = (232, 226, 245, 255)
    badge_size: tuple[int, int] = (18, 18)
    badge_dot_radius: float = 5.0
    reticle_corner_color: tuple[int, int, int, int] = (139, 107, 255, 235)
    reticle_glow_color: tuple[int, int, int, int] = (139, 107, 255, 90)
    reticle_sweep_color: tuple[int, int, int, int] = (232, 226, 245, 200)
    corner_length: int = 10
    corner_thickness: int = 2
    corner_offset: int = 5  # px outside the target rect (ADR-0010: "4〜6px外側")
    canvas_pad: int = 12  # extra room around the corner frame for blur/anti-aliasing


DEFAULT_THEME = Theme()

_SETTLE_SECONDS = 0.32


def corner_position(
    corner: str, screen_width: int, screen_height: int, width: int, height: int, margin: int
) -> tuple[int, int]:
    x = screen_width - width - margin if "right" in corner else margin
    y = screen_height - height - margin if "bottom" in corner else margin
    return x, y


def render_badge(theme: Theme = DEFAULT_THEME, *, delivering: bool) -> RenderedImage:
    width, height = theme.badge_size
    image = Image.new("RGBA", (width, height), theme.badge_background)
    draw = ImageDraw.Draw(image)
    color = theme.badge_dot_delivering if delivering else theme.badge_dot_idle
    cx, cy, r = width / 2, height / 2, theme.badge_dot_radius
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=color)
    return _to_rendered(image)


def reticle_canvas_size(theme: Theme, rect: Rect) -> tuple[int, int]:
    _, _, width, height = rect
    margin = theme.corner_offset + theme.corner_length + theme.canvas_pad
    return width + 2 * margin, height + 2 * margin


def reticle_origin(theme: Theme, rect: Rect) -> tuple[int, int]:
    """Top-left of the reticle canvas in the same coordinate space as ``rect``."""

    left, top, *_ = rect
    margin = theme.corner_offset + theme.corner_length + theme.canvas_pad
    return left - margin, top - margin


def render_reticle(theme: Theme, rect: Rect, *, action: str, progress: float) -> RenderedImage:
    """One frame of the Adaptive Corner Frame settle animation, ``progress`` in [0, 1].

    Shape is shared across action types; only motion differs (ADR-0010's animation grammar):
    click contracts the corners inward then releases, type sweeps a highlight left-to-right,
    select expands the corners outward, wait pulses the glow at a low frequency (looping, so its
    ``progress`` is expected to keep increasing past 1.0 rather than settle).
    """

    progress = max(0.0, progress)
    settle = min(1.0, progress)
    _, _, width, height = rect
    canvas_width, canvas_height = reticle_canvas_size(theme, rect)
    image = Image.new("RGBA", (canvas_width, canvas_height), (0, 0, 0, 0))

    glow_alpha = _glow_alpha(theme, action, progress, settle)
    if glow_alpha > 0:
        glow = Image.new("RGBA", (canvas_width, canvas_height), (0, 0, 0, 0))
        glow_draw = ImageDraw.Draw(glow)
        box = (theme.canvas_pad, theme.canvas_pad, canvas_width - theme.canvas_pad, canvas_height - theme.canvas_pad)
        r, g, b, a = theme.reticle_glow_color
        glow_draw.rounded_rectangle(box, radius=8, fill=(r, g, b, int(a * glow_alpha)))
        glow = glow.filter(ImageFilter.GaussianBlur(radius=6))
        image = Image.alpha_composite(image, glow)

    draw = ImageDraw.Draw(image)
    delta = _corner_offset_delta(action, settle)
    extra_v = _vertical_extra(theme, action, settle)
    # Corner vertices sit ``corner_offset`` outside the rect, which itself sits at canvas-local
    # ``reticle_canvas_size``'s margin (corner_offset + corner_length + canvas_pad) from the
    # canvas edge -- so a vertex's distance from the edge is that margin minus corner_offset,
    # i.e. corner_length + canvas_pad, adjusted by the action's animation delta.
    inset = theme.canvas_pad + theme.corner_length - delta
    left = top = inset
    right = canvas_width - inset
    bottom = canvas_height - inset
    corner_alpha = int(theme.reticle_corner_color[3] * settle)
    color = (*theme.reticle_corner_color[:3], corner_alpha)
    for corner_x, corner_y, dx, dy in (
        (left, top, 1, 1),
        (right, top, -1, 1),
        (left, bottom, 1, -1),
        (right, bottom, -1, -1),
    ):
        _draw_corner(draw, corner_x, corner_y, dx, dy, theme.corner_length, theme.corner_thickness, extra_v, color)

    if action == "type" and settle >= 1.0:
        sweep_x = left + (right - left) * ((progress - 1.0) % 1.0 if progress > 1.0 else 0.0)
        r, g, b, a = theme.reticle_sweep_color
        draw.line([(sweep_x, top), (sweep_x, bottom)], fill=(r, g, b, a), width=theme.corner_thickness)

    return _to_rendered(image)


def _corner_offset_delta(action: str, settle: float) -> float:
    if action == "click":
        bump = math.sin(min(settle, 1.0) * math.pi)  # momentary contraction, peaks mid-settle
        return -3.0 * bump
    if action == "select":
        return 2.0 * settle  # gentle outward expansion
    return 0.0


def _vertical_extra(theme: Theme, action: str, settle: float) -> float:
    if action == "select":
        return theme.corner_length * 0.4 * settle
    return 0.0


def _glow_alpha(theme: Theme, action: str, progress: float, settle: float) -> float:
    if action == "wait":
        return 0.35 + 0.35 * (1 + math.sin(progress * 2 * math.pi * 0.5)) / 2
    return settle


def _draw_corner(
    draw, x: float, y: float, dx: int, dy: int, length: int, thickness: int, extra_v: float, color
) -> None:
    horizontal_end = x + dx * length
    vertical_end = y + dy * (length + extra_v)
    draw.line([(x, y), (horizontal_end, y)], fill=color, width=thickness)
    draw.line([(x, y), (x, vertical_end)], fill=color, width=thickness)


def settle_seconds(theme: Theme = DEFAULT_THEME) -> float:
    return _SETTLE_SECONDS


def _to_rendered(image: Image.Image) -> RenderedImage:
    r, g, b, a = image.convert("RGBA").split()
    premultiplied = Image.merge(
        "RGBA", (ImageChops.multiply(b, a), ImageChops.multiply(g, a), ImageChops.multiply(r, a), a)
    )
    return RenderedImage(image.width, image.height, premultiplied.tobytes())
