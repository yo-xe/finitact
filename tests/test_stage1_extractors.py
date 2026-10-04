import dataclasses

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from finitact.screen_grounded_adapter import VisualRegion, WindowFrame
from finitact.stage1_extractors import (
    CachingRegionExtractor,
    CachingTextRecognizer,
    CompositeRegionExtractor,
    EdgeRectangleRegionExtractor,
    PaddleOcrTextRegionExtractor,
    TesseractTextRegionExtractor,
    _pad_for_detection,
    _unscale_rect,
)

# Tesseract needs a real, reasonably bold font to recognize synthetic fixture text reliably;
# PIL's built-in default font is a tiny bitmap face that OCRs poorly. Path varies by OS, so try
# the common ones rather than hardcoding one (BUG-0008: hardcoded Linux path broke Windows-native).
_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",  # Linux
    r"C:\Windows\Fonts\arialbd.ttf",  # Windows
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",  # macOS
)


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    for path in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    raise OSError(f"no usable bold font found among: {_FONT_CANDIDATES}")


_FONT = _load_font(24)


def _frame_with_text_and_box() -> WindowFrame:
    image = Image.new("RGB", (240, 100), "white")
    draw = ImageDraw.Draw(image)
    draw.text((20, 20), "OK", fill="black", font=_FONT)
    draw.rectangle((140, 30, 190, 70), outline="black", width=4)
    bgra = Image.merge(
        "RGBA",
        (image.getchannel("B"), image.getchannel("G"), image.getchannel("R"), Image.new("L", image.size, 255)),
    )
    return WindowFrame(hwnd=1, process_id=1, width=image.width, height=image.height, pixels=bgra.tobytes())


def test_tesseract_extractor_returns_bounded_evidence_backed_regions():
    frame = _frame_with_text_and_box()
    regions = TesseractTextRegionExtractor().extract(frame)
    assert len(regions) == 1
    region = regions[0]
    assert region.label == "OK"
    assert region.evidence.startswith("tesseract_line:")
    assert 0.0 <= region.confidence <= 1.0
    left, top, width, height = region.rect
    assert 15 <= left <= 25 and width > 0 and height > 0


def _frame_with_a_two_word_line() -> WindowFrame:
    image = Image.new("RGB", (300, 60), "white")
    draw = ImageDraw.Draw(image)
    draw.text((10, 10), "WXGA (1366x768)", fill="black", font=_FONT)
    bgra = Image.merge(
        "RGBA",
        (image.getchannel("B"), image.getchannel("G"), image.getchannel("R"), Image.new("L", image.size, 255)),
    )
    return WindowFrame(hwnd=1, process_id=1, width=image.width, height=image.height, pixels=bgra.tobytes())


def test_tesseract_extractor_merges_same_line_words_into_one_region():
    frame = _frame_with_a_two_word_line()
    regions = TesseractTextRegionExtractor().extract(frame)
    assert len(regions) == 1
    region = regions[0]
    assert region.label == "WXGA (1366x768)"
    assert "words=2" in region.evidence


def test_tesseract_extractor_drops_low_confidence_words():
    frame = _frame_with_text_and_box()
    regions = TesseractTextRegionExtractor(min_confidence=1.1).extract(frame)
    assert regions == ()


def test_edge_extractor_finds_the_drawn_box_independent_of_text():
    frame = _frame_with_text_and_box()
    regions = EdgeRectangleRegionExtractor(min_area=100).extract(frame)
    assert regions
    matches = [r for r in regions if r.rect[0] in range(135, 145) and r.rect[1] in range(25, 35)]
    assert matches, f"expected a region near the drawn box, got {[r.rect for r in regions]}"
    for region in regions:
        assert region.evidence.startswith("edge_contour:")


def test_edge_extractor_respects_min_area_filter():
    frame = _frame_with_text_and_box()
    regions = EdgeRectangleRegionExtractor(min_area=10_000).extract(frame)
    assert regions == ()


def _frame_with_two_boxes_joined_by_a_shared_wall() -> WindowFrame:
    """Two stacked controls sharing a wall (EXP-0001: a list container's left border can flood-
    fill two checkbox rows into one blob) but separated by a long empty gap between them."""

    image = Image.new("RGB", (50, 150), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((10, 5, 30, 25), outline="black", width=1)
    draw.rectangle((10, 105, 30, 125), outline="black", width=1)
    draw.line((10, 5, 10, 125), fill="black", width=1)
    bgra = Image.merge(
        "RGBA",
        (image.getchannel("B"), image.getchannel("G"), image.getchannel("R"), Image.new("L", image.size, 255)),
    )
    return WindowFrame(hwnd=1, process_id=1, width=image.width, height=image.height, pixels=bgra.tobytes())


def test_edge_extractor_splits_a_blob_joined_by_a_shared_wall_into_row_bands():
    frame = _frame_with_two_boxes_joined_by_a_shared_wall()
    regions = EdgeRectangleRegionExtractor(min_area=10).extract(frame)
    assert len(regions) == 2
    tops = sorted(region.rect[1] for region in regions)
    assert tops[0] <= 10
    assert tops[1] >= 100
    # neither region spans the full joined height -- that would mean no split happened.
    assert all(region.rect[3] < 40 for region in regions)


def _bgra_frame(image: Image.Image) -> WindowFrame:
    bgra = Image.merge(
        "RGBA",
        (image.getchannel("B"), image.getchannel("G"), image.getchannel("R"), Image.new("L", image.size, 255)),
    )
    return WindowFrame(hwnd=1, process_id=1, width=image.width, height=image.height, pixels=bgra.tobytes())


def test_tesseract_extractor_splits_a_toolbar_line_at_wide_gaps():
    image = Image.new("RGB", (600, 60), "white")
    draw = ImageDraw.Draw(image)
    draw.text((10, 10), "Clear", fill="black", font=_FONT)
    draw.text((200, 10), "Collapse", fill="black", font=_FONT)
    regions = TesseractTextRegionExtractor().extract(_bgra_frame(image))
    assert sorted(region.label for region in regions) == ["Clear", "Collapse"]


def test_edge_extractor_keeps_an_l_shaped_border_whole():
    image = Image.new("RGB", (80, 60), "white")
    draw = ImageDraw.Draw(image)
    draw.line((10, 10, 10, 40), fill="black", width=1)
    draw.line((10, 40, 50, 40), fill="black", width=1)
    regions = EdgeRectangleRegionExtractor(min_area=10).extract(_bgra_frame(image))
    assert any(region.rect[3] >= 25 and region.rect[2] >= 35 for region in regions), [r.rect for r in regions]


def _small_popup_with_bordered_buttons() -> WindowFrame:
    font = _load_font(16)
    image = Image.new("RGB", (216, 199), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((24, 48, 191, 102), outline="black", width=6)
    draw.text((88, 66), "720P", fill="black", font=font)
    draw.rectangle((24, 118, 191, 172), outline="black", width=6)
    draw.text((70, 136), "FULL HD", fill="black", font=font)
    return _bgra_frame(image)


def test_tesseract_extractor_reads_a_small_popup_only_when_enlarged():
    # BUG-0018: at 1x tesseract returns no words for this layout.
    frame = _small_popup_with_bordered_buttons()
    assert TesseractTextRegionExtractor(max_upscale=1).extract(frame) == ()
    regions = {region.label: region.rect for region in TesseractTextRegionExtractor().extract(frame)}
    assert set(regions) == {"720P", "FULL HD"}
    left, top, width, height = regions["FULL HD"]
    assert 60 <= left <= 80 and 130 <= top <= 145 and width < 100 and height < 25


def test_tesseract_extractor_does_not_enlarge_full_windows():
    extractor = TesseractTextRegionExtractor()
    assert extractor.upscale_factor(1440, 1039) == 1
    assert extractor.upscale_factor(216, 199) == 3
    assert extractor.upscale_factor(50, 40) == 4


def test_unscale_rect_rounds_outward_to_frame_pixels():
    assert _unscale_rect((7, 8, 10, 5), 3) == (2, 2, 4, 3)
    assert _unscale_rect((7, 8, 10, 5), 1) == (7, 8, 10, 5)


class _FakeRapidOcr:
    """Stands in for RapidOCR so PP-OCR grouping is tested on the word boxes it returns (the
    Windows-native model runtime is not installed on every test host)."""

    def __init__(self, word_results):
        self.word_results = word_results

    def __call__(self, image, return_word_box):
        return self


def _word(text, left, right, top=10, bottom=27, conf=1.0):
    return (text, conf, [[left, top], [right, top], [right, bottom], [left, bottom]])


def _paddle_labels(image: Image.Image, word_results) -> list[str]:
    extractor = PaddleOcrTextRegionExtractor()
    extractor._engine = _FakeRapidOcr(word_results)
    return [region.label for region in extractor.extract(_bgra_frame(image))]


def test_paddle_extractor_returns_no_regions_for_the_no_detection_sentinel():
    assert _paddle_labels(Image.new("RGB", (160, 40), (60, 60, 60)), (("", 1.0, None),)) == []


def test_paddle_extractor_splits_adjacent_buttons_at_a_full_height_divider():
    image = Image.new("RGB", (160, 40), (60, 60, 60))
    ImageDraw.Draw(image).line((80, 4, 80, 34), fill=(34, 34, 34))
    words = [[_word("Error", 5, 30), _word("Pause", 36, 67), _word("Editor", 88, 122)]]
    assert _paddle_labels(image, words) == ["Error Pause", "Editor"]


def test_paddle_extractor_keeps_a_label_whole_when_the_gap_has_only_glyph_strokes():
    image = Image.new("RGB", (160, 40), (60, 60, 60))
    ImageDraw.Draw(image).line((80, 14, 80, 24), fill=(200, 200, 200))
    assert _paddle_labels(image, [[_word("Delta", 40, 76), _word("Transform", 84, 140)]]) == ["Delta Transform"]


def test_paddle_extractor_drops_icon_glyph_words_from_the_label():
    image = Image.new("RGB", (160, 40), "white")
    words = [[_word("#", 5, 12), _word("Scene", 18, 60)], [_word("✓", 5, 10, 30, 38), _word("::::", 12, 30, 30, 38)]]
    assert _paddle_labels(image, words) == ["Scene", "✓ ::::"]


def test_paddle_extractor_keeps_a_value_and_its_unit_spaced():
    image = Image.new("RGB", (160, 40), "white")
    assert _paddle_labels(image, [[_word("0", 5, 12), _word("m", 18, 26)]]) == ["0 m"]


def _paddle_ids(image: Image.Image, word_results) -> dict[tuple[str, int], str]:
    extractor = PaddleOcrTextRegionExtractor()
    extractor._engine = _FakeRapidOcr(word_results)
    return {(region.label, region.rect[0] // 40): region.id for region in extractor.extract(_bgra_frame(image))}


def test_paddle_region_ids_survive_box_jitter_and_same_row_y_swaps():
    image = Image.new("RGB", (200, 60), "white")
    before = [[_word("0", 5, 12, 10, 27)], [_word("0", 85, 92, 12, 29)], [_word("Scene", 5, 60, 35, 52)]]
    after = [[_word("0", 6, 13, 13, 29)], [_word("0", 85, 93, 11, 27)], [_word("Scene", 4, 61, 34, 54)]]
    ids = _paddle_ids(image, before)
    assert ids == _paddle_ids(image, after)
    assert len(set(ids.values())) == 3


class _Fixed:
    def __init__(self, *regions):
        self.regions = regions

    def extract(self, frame):
        return self.regions


def test_composite_drops_supplementary_regions_centred_in_a_primary_region():
    text = VisualRegion("t", "Shading", (100, 10, 60, 16), 0.9, "ocr")
    inside = VisualRegion("e1", "boxed_region_58x14", (101, 11, 58, 14), 1.0, "edge_contour")
    icon = VisualRegion("e2", "boxed_region_15x15", (10, 10, 15, 15), 1.0, "edge_contour")
    extractor = CompositeRegionExtractor((_Fixed(text),), supplementary=(_Fixed(inside, icon),))
    assert [region.id for region in extractor.extract(None)] == ["t", "e2"]


def test_caching_extractor_reuses_regions_only_for_pixel_identical_frames():
    calls = []

    class Counting:
        def extract(self, frame):
            calls.append(frame.pixels)
            return (VisualRegion("t", "OK", (0, 0, 1, 1), 0.9, "ocr"),)

    def solid(value):
        return WindowFrame(1, 1, 2, 1, bytes([value, value, value, 255]) * 2)

    extractor = CachingRegionExtractor(Counting(), capacity=2)
    assert extractor.extract(solid(0)) == extractor.extract(solid(0))
    assert len(calls) == 1
    extractor.extract(solid(1))
    extractor.extract(solid(2))  # evicts solid(0), the least recently used
    extractor.extract(solid(0))
    assert len(calls) == 4


@dataclasses.dataclass
class _RecInput:
    img: object
    return_word_box: bool = False


@dataclasses.dataclass
class _RecOutput:
    imgs: object
    txts: tuple
    scores: list
    word_results: tuple
    elapse: float


class _CountingRecognizer:
    def __init__(self):
        self.batches = []

    def __call__(self, args):
        self.batches.append(len(args.img))
        txts = tuple(f"t{int(image[0, 0, 0])}" for image in args.img)
        return _RecOutput(args.img, txts, [0.9] * len(txts), tuple((t,) for t in txts), 0.0)


def _crop(value, width=20):
    return np.full((8, width, 3), value, dtype=np.uint8)


def test_caching_recognizer_recognizes_only_crops_it_has_not_seen():
    inner = _CountingRecognizer()
    recognizer = CachingTextRecognizer(inner)
    first = recognizer(_RecInput([_crop(1), _crop(2)], True))
    second = recognizer(_RecInput([_crop(3), _crop(2), _crop(3), _crop(1)], True))
    assert first.txts == ("t1", "t2")
    assert second.txts == ("t3", "t2", "t3", "t1")
    assert second.word_results == (("t3",), ("t2",), ("t3",), ("t1",))
    assert inner.batches == [2, 1]


def test_caching_recognizer_keys_on_crop_shape_and_word_box_mode():
    inner = _CountingRecognizer()
    recognizer = CachingTextRecognizer(inner)
    recognizer(_RecInput([_crop(1)], True))
    recognizer(_RecInput([_crop(1, width=40)], True))
    recognizer(_RecInput([_crop(1)], False))
    assert inner.batches == [1, 1, 1]


def test_detection_padding_caps_the_upscale_of_a_thin_frame_and_keeps_coordinates():
    thin = np.zeros((39, 300, 3), dtype=np.uint8)
    thin[:] = (40, 40, 40)
    thin[10:20, 10:50] = 255
    padded = _pad_for_detection(thin)
    assert padded.shape == (184, 300, 3)
    assert (padded[:39] == thin).all()
    assert (padded[39:] == 40).all()
    normal = np.zeros((247, 203, 3), dtype=np.uint8)
    assert _pad_for_detection(normal) is normal
