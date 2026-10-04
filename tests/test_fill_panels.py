import numpy as np
from test_screen_grounded_adapter import InputPointer, input_adapter

from finitact.action_adapter import Freshness
from finitact.contracts import FILL_PANEL_SOURCE, Decision, ObservedCandidate
from finitact.fill_panels import panel_blocks
from finitact.runs import _likely_candidates
from finitact.screen_grounded_adapter import VisualRegion, WindowFrame

BODY_A = (30, 30, 40, 8)
BODY_B = (30, 46, 60, 8)
SMALL_BOX = (112, 30, 30, 10)  # its own coloured field inside the panel
MENU = (4, 4, 20, 8)  # on the frame-hugging chrome


def screen(*, panel_bottom=150):
    img = np.full((180, 200, 4), 255, np.uint8)
    img[:, :, :3] = 60
    img[20:panel_bottom, 20:180, :3] = 30
    img[24:44, 106:150, :3] = 90
    for x, y, w, h in (BODY_A, BODY_B, SMALL_BOX, MENU):
        img[y + 2 : y + h - 2, x + 2 : x + w - 2, :3] = 220
    return img


def rects():
    return [BODY_A, BODY_B, SMALL_BOX, MENU]


def test_lines_on_one_panel_form_one_block_and_other_fields_stay_apart():
    blocks = panel_blocks(screen(), rects())
    assert [(block.rect, block.members) for block in blocks] == [((20, 20, 160, 128), (0, 1))]
    x, y = blocks[0].delivery_point
    assert y > BODY_B[1] + BODY_B[3] and 20 < x < 180
    for rx, ry, rw, rh in rects():
        assert not (rx - 2 <= x < rx + rw + 2 and ry - 2 <= y < ry + rh + 2)


def test_no_free_panel_cell_means_no_block_rather_than_a_centre_click():
    # An ineligible region (e.g. an edge contour) still keeps the delivery point off its area.
    covering = rects() + [(20, 20, 160, 130)]
    assert panel_blocks(screen(), covering, [True, True, True, True, False]) == []


def test_a_single_line_strip_is_not_a_panel():
    assert panel_blocks(screen(panel_bottom=36), [(30, 22, 40, 12)]) == []


def as_frame(img):
    return WindowFrame(100, 7, img.shape[1], img.shape[0], img.tobytes())


class PanelExtractor:
    def extract(self, actual):
        return tuple(
            VisualRegion(f"r{index}", label, rect, 0.9, "template:r")
            for index, (label, rect) in enumerate(zip(("OLDVALUE", "print()", "Search", "File"), rects()))
        )


def panel_adapter(*images, pointer):
    subject, _ = input_adapter(*(as_frame(img) for img in images), pointer=pointer, operations=("click", "fill"))
    subject.extractor = PanelExtractor()
    return subject


def test_panel_fill_replaces_member_fills_and_is_delivered_at_its_own_point():
    pointer = InputPointer()
    subject = panel_adapter(screen(), screen(), pointer=pointer)
    observation = subject.observe()
    fills = [candidate for candidate in observation.candidates if candidate.operation == "fill"]
    assert [candidate.label for candidate in fills] == ["Search", "File", "OLDVALUE / print()"]
    assert len([c for c in observation.candidates if c.operation == "click"]) == 4
    panel = fills[-1]
    assert panel.attributes["source"] == FILL_PANEL_SOURCE and panel.attributes["rect"] == (20, 20, 160, 128)
    assert subject.act(panel, observation, text="new").status == "confirmed"
    point = panel.binding["delivery_point"]
    assert pointer.checks == [(100, 7, point)] and pointer.typed == [(100, 7, point, "new")]


def test_panel_that_changed_under_unchanged_text_is_not_delivered():
    pointer = InputPointer()
    subject = panel_adapter(screen(), screen(panel_bottom=100), screen(panel_bottom=100), pointer=pointer)
    observation = subject.observe()
    panel = next(c for c in observation.candidates if c.attributes["source"] == FILL_PANEL_SOURCE)
    assert subject.fresh(observation, panel) is Freshness.STALE
    assert subject.act(panel, observation, text="new").status != "confirmed"
    assert pointer.typed == []


def test_uncertain_list_keeps_panels_that_share_a_cut_label_but_not_repeated_lines():
    def fill(id, label, rect, source="ppocrv6_medium_line"):
        return ObservedCandidate(id, "fill", label, None, {"rect": rect, "source": source})

    candidates = [
        fill("p1", "same", (0, 0, 100, 100), FILL_PANEL_SOURCE),
        fill("p2", "same", (200, 0, 100, 100), FILL_PANEL_SOURCE),
        fill("p3", "same", (0, 0, 100, 100), FILL_PANEL_SOURCE),
        fill("l1", "line", (0, 200, 40, 20)),
        fill("l2", "line", (0, 300, 40, 20)),
    ]
    decision = Decision(
        terminal_reason="uncertain",
        provider_metadata={"probabilities": {c.id: 0.2 - i / 100 for i, c in enumerate(candidates)}},
    )
    assert [item["ref"] for item in _likely_candidates(decision, candidates)] == ["p1", "p2", "l1"]


CAPTION, FIELD, BELOW = (31, 31, 53, 20), (97, 30, 88, 21), (25, 82, 101, 20)  # Tk live, BUG-0034


class FormExtractor:
    def __init__(self, regions):
        self.regions = regions

    def extract(self, actual):
        return tuple(
            VisualRegion(f"r{index}", label, rect, 0.9, f"{source}:r{index}")
            for index, (label, rect, source) in enumerate(self.regions)
        )


def form_adapter(regions, *images, pointer):
    subject, _ = input_adapter(*(as_frame(img) for img in images), pointer=pointer, operations=("click", "fill"))
    subject.extractor = FormExtractor(regions)
    return subject


def form(*extra):
    return [("Name:", CAPTION, "ocr"), ("OLDVALUE", FIELD, "ocr"), ("Name order", BELOW, "ocr"), *extra]


def blank(shade=200):
    return np.full((300, 300, 4), shade, np.uint8)


def test_caption_names_the_field_fill_and_is_no_fill_target_itself():
    pointer = InputPointer()
    subject = form_adapter(form(), blank(), blank(201), blank(201), pointer=pointer)
    observation = subject.observe()
    fills = [c for c in observation.candidates if c.operation == "fill"]
    assert [(c.label, c.attributes["rect"]) for c in fills] == [("Name:", FIELD), ("Name order", BELOW)]
    # The field's text is its current value, routed as untrusted content (BUG-0034).
    assert observation.untrusted_values == {fills[0].id: "OLDVALUE"}
    assert "Name:" in [c.label for c in observation.candidates if c.operation == "click"]
    uncertain = Decision(terminal_reason="uncertain", provider_metadata={"probabilities": {fills[0].id: 0.4}})
    assert _likely_candidates(uncertain, fills, observation.untrusted_values)[0]["current_value"] == "OLDVALUE"
    # A changed frame is re-extracted, and the captioned target is re-identified by its caption and field.
    assert subject.act(fills[0], observation, text="new").status == "confirmed"
    assert pointer.typed == [(100, 7, (141, 40), "new")]


def test_captioned_fill_is_not_delivered_once_its_caption_is_gone():
    pointer = InputPointer()
    subject = form_adapter(form(), blank(), blank(201), blank(201), pointer=pointer)
    observation = subject.observe()
    fill = next(c for c in observation.candidates if c.operation == "fill")
    subject.extractor = FormExtractor(form()[1:])
    assert subject.act(fill, observation, text="new").status != "confirmed"
    assert pointer.typed == []


def test_ambiguous_or_distant_captions_stay_separate_fills():
    cases = {
        # Two regions within reach on the row: no unique field.
        "two fields": form(("box", (92, 27, 100, 27), "edge_contour")),
        # The caption is the value inside another box, e.g. an Entry that holds "Name:".
        "caption inside a box": form(("box", (20, 25, 180, 32), "edge_contour")),
        "too far": [("Name:", CAPTION, "ocr"), ("OLDVALUE", (200, 30, 88, 21), "ocr")],
        "other row": [("Name:", CAPTION, "ocr"), ("OLDVALUE", (97, 48, 88, 21), "ocr")],
        "no colon": [("Name", CAPTION, "ocr"), ("OLDVALUE", FIELD, "ocr")],
    }
    for name, regions in cases.items():
        subject = form_adapter(regions, blank(), pointer=InputPointer())
        fills = [c for c in subject.observe().candidates if c.operation == "fill"]
        assert not any("caption" in c.binding for c in fills), name
        assert len(fills) == len(regions), name
