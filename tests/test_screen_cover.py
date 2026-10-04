import numpy as np

from finitact.screen_cover import uncovered_crop
from finitact.screen_grounded_adapter import WindowFrame


def text_frame(width=128, height=64, *, text=(80, 16, 32, 24)):
    pixels = np.full((height, width, 4), 255, np.uint8)
    left, top, w, h = text
    pixels[top : top + h, left : left + w : 3, :3] = 0
    return WindowFrame(100, 7, width, height, pixels.tobytes())


def test_the_crop_holds_only_the_uncovered_text_and_paints_the_rest():
    crop = uncovered_crop(text_frame(), [(0, 0, 64, 64)])
    assert crop is not None
    left, top = crop.offset
    assert left >= 64 and left <= 80 and top <= 16
    assert left + crop.frame.width >= 112 and top + crop.frame.height >= 40
    pixels = np.frombuffer(crop.frame.pixels, np.uint8).reshape(crop.frame.height, crop.frame.width, 4)
    assert (pixels[:, :, :3] == 0).any()


def test_nothing_is_read_where_uia_covers_all_or_the_rest_is_blank_or_too_narrow():
    assert uncovered_crop(text_frame(), [(0, 0, 128, 64)]) is None
    assert uncovered_crop(text_frame(text=(0, 0, 1, 1)), [(0, 0, 64, 64)]) is None
    # A 20px gap between two elements is padding, not a line of text.
    assert uncovered_crop(text_frame(text=(66, 0, 18, 64)), [(0, 0, 64, 64), (84, 0, 44, 64)]) is None
