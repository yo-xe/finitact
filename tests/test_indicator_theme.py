from PIL import Image

from finitact.indicator_theme import (
    DEFAULT_THEME,
    corner_position,
    render_badge,
    render_reticle,
    reticle_canvas_size,
    reticle_origin,
    settle_seconds,
)


def test_corner_position_bottom_right_hugs_the_far_edges():
    x, y = corner_position("bottom_right", 1920, 1080, 200, 40, margin=20)
    assert (x, y) == (1920 - 200 - 20, 1080 - 40 - 20)


def test_corner_position_top_left_hugs_the_near_edges():
    x, y = corner_position("top_left", 1920, 1080, 200, 40, margin=20)
    assert (x, y) == (20, 20)


def test_render_badge_matches_the_configured_size():
    image = render_badge(DEFAULT_THEME, delivering=False)
    assert (image.width, image.height) == DEFAULT_THEME.badge_size
    assert len(image.bgra_premultiplied) == image.width * image.height * 4


def test_render_badge_delivering_differs_from_idle():
    idle = render_badge(DEFAULT_THEME, delivering=False)
    delivering = render_badge(DEFAULT_THEME, delivering=True)
    assert idle.bgra_premultiplied != delivering.bgra_premultiplied


def test_render_reticle_at_zero_progress_is_fully_transparent():
    # settle == 0 means zero corner alpha and (for non-"wait" actions) zero glow alpha, so the
    # whole premultiplied canvas must collapse to (0, 0, 0, 0).
    image = render_reticle(DEFAULT_THEME, (0, 0, 30, 15), action="click", progress=0.0)
    assert image.bgra_premultiplied == bytes(len(image.bgra_premultiplied))


def test_reticle_canvas_size_and_origin_bracket_the_target_rect():
    rect = (100, 200, 40, 20)
    width, height = reticle_canvas_size(DEFAULT_THEME, rect)
    origin_x, origin_y = reticle_origin(DEFAULT_THEME, rect)
    margin = DEFAULT_THEME.corner_offset + DEFAULT_THEME.corner_length + DEFAULT_THEME.canvas_pad
    assert (width, height) == (40 + 2 * margin, 20 + 2 * margin)
    assert (origin_x, origin_y) == (100 - margin, 200 - margin)


def test_render_reticle_produces_a_canvas_sized_image():
    rect = (0, 0, 30, 15)
    image = render_reticle(DEFAULT_THEME, rect, action="click", progress=0.5)
    assert (image.width, image.height) == reticle_canvas_size(DEFAULT_THEME, rect)


def test_render_reticle_settles_to_a_stable_frame_past_full_progress():
    rect = (0, 0, 30, 15)
    at_settle = render_reticle(DEFAULT_THEME, rect, action="select", progress=1.0)
    past_settle = render_reticle(DEFAULT_THEME, rect, action="select", progress=1.5)
    assert at_settle.bgra_premultiplied == past_settle.bgra_premultiplied


def test_render_reticle_click_and_select_diverge_mid_settle():
    rect = (0, 0, 30, 15)
    click = render_reticle(DEFAULT_THEME, rect, action="click", progress=0.5)
    select = render_reticle(DEFAULT_THEME, rect, action="select", progress=0.5)
    assert click.bgra_premultiplied != select.bgra_premultiplied


def test_render_reticle_wait_keeps_pulsing_past_full_progress():
    rect = (0, 0, 30, 15)
    first = render_reticle(DEFAULT_THEME, rect, action="wait", progress=1.0)
    later = render_reticle(DEFAULT_THEME, rect, action="wait", progress=1.5)
    assert first.bgra_premultiplied != later.bgra_premultiplied


def test_settle_seconds_is_positive():
    assert settle_seconds(DEFAULT_THEME) > 0


def test_render_reticle_draws_all_four_corners_not_just_one():
    # Regression: the corner-vertex inset once omitted `corner_length`, so only the top-left
    # corner landed inside the canvas and the other three fell on background pixels far outside
    # it -- caught by sampling real screen pixels during a live Win32 run, not by these unit
    # tests alone, since they never checked more than one corner's position.
    rect = (0, 0, 200, 100)
    image = render_reticle(DEFAULT_THEME, rect, action="click", progress=1.0)
    canvas = Image.frombytes("RGBA", (image.width, image.height), image.bgra_premultiplied, "raw", "BGRA")
    inset = DEFAULT_THEME.canvas_pad + DEFAULT_THEME.corner_length
    probe_offset = 3  # a few px in from the vertex, along the arm, away from anti-aliased edges
    corners = {
        "top_left": (inset + probe_offset, inset),
        "top_right": (image.width - inset - probe_offset, inset),
        "bottom_left": (inset + probe_offset, image.height - inset),
        "bottom_right": (image.width - inset - probe_offset, image.height - inset),
    }
    for name, (x, y) in corners.items():
        alpha = canvas.getpixel((x, y))[3]
        assert alpha > 0, f"{name} corner did not render at {(x, y)}"
