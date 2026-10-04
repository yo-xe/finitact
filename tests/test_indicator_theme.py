from dataclasses import replace

import pytest
from PIL import Image

from finitact.indicator_theme import (
    DEFAULT_THEME,
    ProviderState,
    corner_position,
    render_orb,
    render_reticle,
    reticle_canvas_size,
    reticle_origin,
    theme_from_env,
)


def test_corner_position_bottom_right_hugs_the_far_edges():
    x, y = corner_position("bottom_right", 1920, 1080, 200, 40, margin=20)
    assert (x, y) == (1920 - 200 - 20, 1080 - 40 - 20)


def test_corner_position_top_left_hugs_the_near_edges():
    x, y = corner_position("top_left", 1920, 1080, 200, 40, margin=20)
    assert (x, y) == (20, 20)


def test_render_orb_matches_the_theme_size():
    image = render_orb(DEFAULT_THEME, "idle", 0.0)
    assert (image.width, image.height) == (DEFAULT_THEME.orb_size, DEFAULT_THEME.orb_size)
    assert len(image.bgra_premultiplied) == image.width * image.height * 4


def test_render_orb_moves_differently_per_phase():
    phases = ("idle", "observe", "think", "click")
    frames = {phase: render_orb(DEFAULT_THEME, phase, 0.4).bgra_premultiplied for phase in phases}
    assert len(set(frames.values())) == len(frames)


def test_render_orb_animates_within_a_phase():
    assert render_orb(DEFAULT_THEME, "think", 0.0).bgra_premultiplied != render_orb(
        DEFAULT_THEME, "think", 0.3
    ).bgra_premultiplied


def test_theme_from_env_overrides_size_and_colors():
    theme = theme_from_env(
        {
            "FINITACT_INDICATOR_SIZE": "96",
            "FINITACT_INDICATOR_COLOR_THINK": "#102030",
            "FINITACT_INDICATOR_COLOR_RETICLE": "#ff8800",
            "FINITACT_INDICATOR_COLOR_PROVIDER": "#00ff00",
        }
    )
    assert theme.orb_size == 96
    assert dict(theme.orb_rim)["think"] == (16, 32, 48)
    assert dict(theme.orb_rim)["idle"] == dict(DEFAULT_THEME.orb_rim)["idle"]
    assert theme.reticle_color == (255, 136, 0)
    assert theme.provider_color == (0, 255, 0)


def test_theme_from_env_without_overrides_is_the_default():
    assert theme_from_env({}) == DEFAULT_THEME


@pytest.mark.parametrize(
    ("name", "value"),
    [("SIZE", "12"), ("SIZE", "400"), ("SIZE", "big"), ("COLOR_IDLE", "indigo"), ("COLOR_CORE", "#12345")],
)
def test_theme_from_env_rejects_malformed_values(name, value):
    with pytest.raises(ValueError, match=f"FINITACT_INDICATOR_{name}"):
        theme_from_env({f"FINITACT_INDICATOR_{name}": value})


def test_render_reticle_at_time_zero_is_fully_transparent():
    image = render_reticle(DEFAULT_THEME, (0, 0, 30, 15), action="click", t=0.0)
    assert image.bgra_premultiplied == bytes(len(image.bgra_premultiplied))


def test_reticle_canvas_is_a_bounded_square_centered_on_the_target():
    rect = (100, 200, 40, 20)
    width, height = reticle_canvas_size(DEFAULT_THEME, rect)
    origin_x, origin_y = reticle_origin(DEFAULT_THEME, rect)
    assert width == height
    assert 44 <= width <= 96
    assert (origin_x + width // 2, origin_y + height // 2) == (120, 210)
    assert reticle_canvas_size(DEFAULT_THEME, (0, 0, 2000, 900))[0] == 96


def test_render_reticle_produces_a_canvas_sized_image():
    rect = (0, 0, 30, 15)
    image = render_reticle(DEFAULT_THEME, rect, action="click", t=0.5)
    assert (image.width, image.height) == reticle_canvas_size(DEFAULT_THEME, rect)


def test_render_reticle_actions_move_differently():
    rect = (0, 0, 30, 15)
    times = [0.6 + 0.1 * step for step in range(10)]
    frames = {
        action: b"".join(render_reticle(DEFAULT_THEME, rect, action=action, t=t).bgra_premultiplied for t in times)
        for action in ("click", "fill", "key", "hover")
    }
    assert len(set(frames.values())) == len(frames)


def test_render_reticle_is_a_translucent_orb_over_the_target():
    rect = (0, 0, 200, 40)
    image = render_reticle(DEFAULT_THEME, rect, action="wait", t=1.0)
    canvas = Image.frombytes("RGBA", (image.width, image.height), image.bgra_premultiplied, "raw", "BGRA")
    center = canvas.getpixel((image.width // 2, image.height // 2))[3]
    assert 100 < center < 255
    assert canvas.getpixel((0, 0))[3] < 10


def test_render_reticle_follows_the_orb_act_color_unless_overridden():
    rect = (0, 0, 60, 30)
    default = render_reticle(DEFAULT_THEME, rect, action="click", t=1.0)
    recolored = render_reticle(replace(DEFAULT_THEME, reticle_color=(255, 0, 0)), rect, action="click", t=1.0)
    assert default.bgra_premultiplied != recolored.bgra_premultiplied


def test_render_orb_lights_the_provider_spark_only_while_a_request_is_in_flight():
    idle = render_orb(DEFAULT_THEME, "think", 1.0, ProviderState(False, 5.0))
    asking = render_orb(DEFAULT_THEME, "think", 1.0, ProviderState(True, 1.0))
    size = DEFAULT_THEME.orb_size
    spark = int(size * 0.17)

    def alpha(image):
        canvas = Image.frombytes("RGBA", (size, size), image.bgra_premultiplied, "raw", "BGRA")
        return canvas.getpixel((spark, spark))[3]

    assert alpha(asking) > alpha(idle) + 60


def test_theme_from_env_turns_animation_off():
    assert theme_from_env({"FINITACT_INDICATOR_ANIMATION": "0"}).animated is False
    with pytest.raises(ValueError, match="FINITACT_INDICATOR_ANIMATION"):
        theme_from_env({"FINITACT_INDICATOR_ANIMATION": "off"})


def test_render_orb_gives_every_operation_its_own_motion():
    operations = ("click", "double_click", "right_click", "middle_click", "ctrl_click", "shift_click", "hover",
                  "fill", "key", "scroll_up", "scroll_down", "drag", "set_range")
    times = [0.6 + 0.1 * step for step in range(10)]
    frames = {op: b"".join(render_orb(DEFAULT_THEME, op, t, solo=True).bgra_premultiplied for t in times)
              for op in operations}
    assert len(set(frames.values())) == len(operations)


def test_render_reticle_drag_follows_its_heading():
    rect = (0, 0, 40, 20)
    right = render_reticle(DEFAULT_THEME, rect, action="drag", t=1.0, heading=(1.0, 0.0))
    down = render_reticle(DEFAULT_THEME, rect, action="drag", t=1.0, heading=(0.0, 1.0))
    assert right.bgra_premultiplied != down.bgra_premultiplied
