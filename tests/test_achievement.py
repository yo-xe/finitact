from dataclasses import replace

from finitact.achievement import ArrivalFitVerifier, EffectFitVerifier
from finitact.action_adapter import ActionOutcome, Observation
from finitact.contracts import ObservedCandidate
from finitact.screen_grounded_adapter import ScreenEvidence, VisualRegion, WindowFrame

WIDTH, HEIGHT = 200, 100
FIELD = (20, 20, 100, 20)


def screen(labels, painted=()):
    pixels = bytearray(bytes([255, 255, 255, 255]) * WIDTH * HEIGHT)
    for x, y, w, h in painted:
        for row in range(y, y + h):
            start = (row * WIDTH + x) * 4
            pixels[start : start + w * 4] = bytes([0, 0, 0, 255]) * w
    regions = tuple(VisualRegion(f"r{i}", label, rect, 0.9, "ocr:x") for i, (label, rect) in enumerate(labels))
    return ScreenEvidence(WindowFrame(1, 1, WIDTH, HEIGHT, bytes(pixels)), regions, (), (), ())


def outcome(operation, before, after, text=None):
    candidate = ObservedCandidate("c1", operation, "OLDVALUE", "s", attributes={"rect": FIELD})
    observation = Observation("o", "s", (candidate,), True)
    return ActionOutcome("Replace the text with 'Blue Harbor'.", candidate, text, observation, observation,
                         before, after, [], None, "achieved-1")


def fit(answer):
    asked = []

    def judge(goal, before_view, action, _outcome):
        asked.append((goal, before_view, action))
        if isinstance(answer, Exception):
            raise answer
        return answer

    return judge, asked


BEFORE = screen([("OLDVALUE", FIELD)])
FILLED = screen([("Blue Harbor", FIELD)], painted=[(24, 24, 60, 12)])


def test_fill_whose_value_landed_on_its_target_and_fits_is_achieved():
    judge, asked = fit(True)
    assert ArrivalFitVerifier(judge)(outcome("fill", BEFORE, FILLED, "Blue Harbor")) is True
    goal, before_view, action = asked[0]
    assert before_view["text_regions"] == [["OLDVALUE", *FIELD]]
    assert action["target_part"].startswith("outside_panels")


def test_fill_whose_click_put_the_caret_in_an_input_tells_jev_so_and_others_do_not():
    judge, asked = fit(True)
    ArrivalFitVerifier(judge)(replace(outcome("fill", BEFORE, FILLED, "Blue Harbor"), input_focus=True))
    ArrivalFitVerifier(judge)(outcome("fill", BEFORE, FILLED, "Blue Harbor"))
    assert "text caret" in asked[0][2]["input_focus"]
    assert "input_focus" not in asked[1][2]


def test_landed_action_that_does_not_fit_stays_unverified_not_failed():
    judge, _ = fit(False)
    assert ArrivalFitVerifier(judge)(outcome("fill", BEFORE, FILLED, "Blue Harbor")) is None


def test_value_shown_away_from_the_target_is_not_arrival_and_jev_is_not_asked():
    elsewhere = screen([("OLDVALUE", FIELD), ("Blue Harbor", (20, 70, 100, 20))], painted=[(24, 74, 60, 12)])
    judge, asked = fit(True)
    assert ArrivalFitVerifier(judge)(outcome("fill", BEFORE, elsewhere, "Blue Harbor")) is None
    assert asked == []


def test_click_repainting_its_target_stays_unverified_without_a_terminal_state():
    judge, asked = fit(True)
    repainted = screen([("OLDVALUE", FIELD)], painted=[(24, 24, 60, 12)])
    assert ArrivalFitVerifier(judge)(outcome("click", BEFORE, repainted)) is None
    assert asked == []


def test_click_that_replaces_target_text_can_still_be_verified():
    judge, asked = fit(True)
    changed = screen([("Opened", FIELD)], painted=[(24, 24, 60, 12)])
    assert ArrivalFitVerifier(judge)(outcome("click", BEFORE, changed)) is True
    assert len(asked) == 1


def test_wrong_row_selection_highlight_does_not_become_success_even_if_fit_is_true():
    judge, asked = fit(True)
    before = screen([("Item 15 entry", FIELD)])
    after = screen([("Item 15 entry", FIELD)], painted=[(20, 20, 100, 20)])
    base = replace(outcome("click", before, after), goal="Scroll down until TARGET ROW is visible, then select it")
    assert ArrivalFitVerifier(judge)(base) is None
    assert asked == []

    target = replace(base, candidate=replace(base.candidate, label="TARGET ROW"),
                     before_screen=screen([("TARGET ROW", FIELD)]),
                     after_screen=screen([("TARGET ROW", FIELD)], painted=[(20, 20, 100, 20)]))
    assert ArrivalFitVerifier(judge)(target) is None


def test_synonym_target_with_observed_text_transition_is_still_eligible():
    judge, _ = fit(True)
    base = replace(outcome("click", BEFORE, screen([("Preferences opened", FIELD)], painted=[FIELD])),
                   goal="Open Settings")
    assert ArrivalFitVerifier(judge)(base) is True


def test_provider_failure_and_unmeasured_operations_give_no_verdict():
    judge, _ = fit(RuntimeError("budget"))
    assert ArrivalFitVerifier(judge)(outcome("fill", BEFORE, FILLED, "Blue Harbor")) is None
    judge, asked = fit(True)
    assert ArrivalFitVerifier(judge)(outcome("key", BEFORE, FILLED)) is None
    assert ArrivalFitVerifier(judge)(outcome("drag", BEFORE, FILLED)) is None
    assert ArrivalFitVerifier(judge)(outcome("fill", BEFORE, None, "Blue Harbor")) is None
    assert asked == []


def test_captioned_fill_tells_jev_the_fields_own_text_and_its_caption():
    judge, asked = fit(True)
    base = outcome("fill", BEFORE, FILLED, text="Blue Harbor")
    candidate = ObservedCandidate("c1", "fill", "First:", "s", attributes={"rect": FIELD},
                                  binding={"field_label": "OLDVALUE", "caption": ("r9", "First:", (0, 20, 18, 20), "ocr:r9")})
    assert ArrivalFitVerifier(judge)(replace(base, candidate=candidate)) is True
    action = asked[0][2]
    assert action["target_current_value"] == "OLDVALUE"
    assert action["target_part"] == "caption_field_pair ['First:', 'OLDVALUE']"


LIST = (20, 10, 100, 80)
ROWS = [f"Item {n:02d}" for n in range(1, 13)]


def list_screen(first):
    return screen([(ROWS[i], (24, 12 + (i - first) * 16, 60, 12)) for i in range(first, min(first + 5, len(ROWS)))])


def scroll_outcome(before, after, direction="down"):
    candidate = ObservedCandidate("c2", "scroll", "Scroll down the list", "s",
                                  attributes={"rect": LIST, "direction": direction, "notches": 1})
    return replace(outcome("fill", before, after), candidate=candidate, goal="Bring 'Item 07' into view.")


def test_scroll_whose_rows_moved_tells_jev_what_came_into_view():
    judge, asked = fit(True)
    assert ArrivalFitVerifier(judge)(scroll_outcome(list_screen(0), list_screen(3))) is True
    action = asked[0][2]
    assert action["direction"] == "down"
    assert action["rows_brought_into_view"] == ["Item 06", "Item 07", "Item 08"]


def test_scroll_that_moved_nothing_or_moved_the_other_way_is_not_arrival():
    judge, asked = fit(True)
    assert ArrivalFitVerifier(judge)(scroll_outcome(list_screen(7), list_screen(7))) is None
    assert ArrivalFitVerifier(judge)(scroll_outcome(list_screen(3), list_screen(0))) is None
    assert asked == []


def test_repeated_row_texts_on_a_still_list_do_not_count_as_movement():
    judge, _ = fit(True)
    same = screen([("OK", (24, 12 + i * 16, 60, 12)) for i in range(5)])
    assert ArrivalFitVerifier(judge)(scroll_outcome(same, same)) is None


def test_scroll_replacing_every_row_is_arrival():
    judge, _ = fit(True)
    assert ArrivalFitVerifier(judge)(scroll_outcome(list_screen(0), list_screen(6))) is True



def relabel(shown, changed):
    return screen([(r.label + "x" if r.label in changed else r.label, r.rect) for r in shown.regions])


def test_scroll_keeping_two_rows_is_arrival_when_the_edge_row_reads_differently():
    judge, _ = fit(True)
    assert ArrivalFitVerifier(judge)(scroll_outcome(relabel(list_screen(0), {"Item 05"}), list_screen(3))) is True


def test_still_list_whose_rows_mostly_read_differently_is_not_arrival():
    judge, asked = fit(True)
    after = relabel(list_screen(3), {"Item 04", "Item 05", "Item 07", "Item 08"})
    assert ArrivalFitVerifier(judge)(scroll_outcome(list_screen(3), after)) is None
    assert asked == []


def committed(operation, label, before, after, *, windows=(None, None), goal_text=None):
    candidate = ObservedCandidate("c1", operation, label, "s", attributes={"rect": FIELD} if operation != "key" else {"key": "Enter"})
    observation = Observation("o", "s", (candidate,), True)
    return ActionOutcome("Open Discord and post the message.", candidate, None, observation, observation, before, after,
                         [], None, "achieved-1", windows_before=windows[0], windows_after=windows[1], goal_text=goal_text)


DESKTOP = screen([("Discord", FIELD)])
TERMINAL = {"hwnd": 1, "title": "Windows PowerShell", "process": "powershell.exe", "foreground": True}


def test_launch_counts_only_a_new_foreground_window_named_like_the_clicked_label():
    from finitact.achievement import TerminalEvidenceVerifier

    opened = {"hwnd": 2, "title": "#test | esp32 - Discord", "process": "Discord.exe", "foreground": True}
    judge, asked = fit(True)
    verdict = TerminalEvidenceVerifier(judge)(committed("double_click", "Discord", DESKTOP, DESKTOP,
                                                        windows=((TERMINAL,), (TERMINAL | {"foreground": False}, opened))))
    assert verdict is True and asked[0][2]["operation"] == "double_click"
    error = {"hwnd": 3, "title": "Error", "process": "WerFault.exe", "foreground": True}
    judge, asked = fit(True)
    assert TerminalEvidenceVerifier(judge)(committed("double_click", "Discord", DESKTOP, DESKTOP,
                                                     windows=((TERMINAL,), (error,)))) is None
    assert asked == []


def test_send_counts_the_goal_text_leaving_its_field_for_another_place():
    from finitact.achievement import TerminalEvidenceVerifier

    typed = screen([("f i n i t a c t 比較テスト 1790", (20, 80, 100, 20))])
    sent = screen([("finitact 比較テス卜 1790", (20, 40, 100, 20))])
    judge, _ = fit(True)
    assert TerminalEvidenceVerifier(judge)(committed("key", "Press Enter", typed, sent, goal_text="finitact比較テスト 1790"))
    still = screen([("finitact比較テスト 1790", (20, 80, 100, 20)), ("finitact比較テスト 1790", (20, 40, 100, 20))])
    judge, asked = fit(True)
    assert TerminalEvidenceVerifier(judge)(committed("key", "Press Enter", typed, still, goal_text="finitact比較テスト 1790")) is None
    judge, _ = fit(False)
    assert TerminalEvidenceVerifier(judge)(committed("key", "Press Enter", typed, sent, goal_text="finitact比較テスト 1790")) is None


def test_key_from_the_search_pane_counts_the_window_it_brings_forward_even_when_already_open():
    from finitact.achievement import TerminalEvidenceVerifier

    search = {"hwnd": 5, "title": "検索", "process": "SearchHost.exe", "foreground": True}
    settings = {"hwnd": 6, "title": "設定", "process": "ApplicationFrameHost.exe", "foreground": False}
    tray = {"hwnd": 7, "title": "", "process": "explorer.exe", "foreground": False}
    judge, asked = fit(True)
    raised = ((search, settings), (settings | {"foreground": True},))
    assert TerminalEvidenceVerifier(judge)(committed("key", "Press Enter", None, None, windows=raised)) is True
    assert asked[0][2]["key"] == "Enter" and asked[0][2]["opened_window"] == "設定"
    judge, asked = fit(True)
    for windows in (((search, settings), (search, settings)),
                    ((search, settings), (tray | {"foreground": True}, settings)),
                    ((TERMINAL, settings), (TERMINAL | {"foreground": False}, settings | {"foreground": True}))):
        assert TerminalEvidenceVerifier(judge)(committed("key", "Press Enter", None, None, windows=windows)) is None
    assert asked == []


def test_launch_is_judged_without_ocr_evidence_from_the_offered_targets():
    from finitact.achievement import TerminalEvidenceVerifier

    opened = {"hwnd": 2, "title": "Discord", "process": "Discord.exe", "foreground": True}
    judge, asked = fit(True)
    outcome = committed("double_click", "Discord", None, None, windows=((TERMINAL,), (opened,)))
    assert TerminalEvidenceVerifier(judge)(outcome) is True
    assert asked[0][1] == {"text_regions": [["Discord", *FIELD]]}


def test_effect_fit_asks_only_when_the_post_condition_is_met():
    judge, asked = fit(True)
    base = replace(outcome("fill", None, None, "42500"), before_view={"url": "u"})
    met = {"expected": {"value": "42500"}, "before": "", "after": "42500", "effect": "met"}
    assert EffectFitVerifier(judge)(replace(base, effect=met)) is True
    assert asked[0][2]["text"] == "42500" and asked[0][1] == {"url": "u"}
    for effect in (None, {**met, "effect": "unknown"}, {**met, "effect": "not_met"}):
        assert EffectFitVerifier(judge)(replace(base, effect=effect)) is None
    assert EffectFitVerifier(judge)(replace(base, effect=met, before_view=None)) is None
    assert len(asked) == 1


def test_effect_fit_without_a_fit_or_verdict_is_unverified():
    base = replace(outcome("fill", None, None, "x"), before_view={}, effect={"effect": "met"})
    assert EffectFitVerifier(fit(False)[0])(base) is None
    assert EffectFitVerifier(fit(RuntimeError("down"))[0])(base) is None


def screen_hwnd(hwnd, labels, painted=()):
    pixels = bytearray(bytes([255, 255, 255, 255]) * WIDTH * HEIGHT)
    for x, y, w, h in painted:
        for row in range(y, y + h):
            start = (row * WIDTH + x) * 4
            pixels[start : start + w * 4] = bytes([0, 0, 0, 255]) * w
    regions = tuple(VisualRegion(f"r{i}", label, rect, 0.9, "ocr:x") for i, (label, rect) in enumerate(labels))
    return ScreenEvidence(WindowFrame(hwnd, 1, WIDTH, HEIGHT, bytes(pixels)), regions, (), (), ())


def popup_outcome(before_popup, after_root, before_anchor_root, label="16:9 Aspect"):
    candidate = ObservedCandidate(
        "c1", "click", label, "s", attributes={"rect": FIELD, "scope": "owned_popup", "scope_hwnd": 2}
    )
    observation = Observation("o", "s", (candidate,), True)
    return ActionOutcome(
        "Set the resolution to 16:9 Aspect.", candidate, None, observation, observation,
        before_popup, after_root, [], None, "achieved-1", before_anchor_screen=before_anchor_root,
    )


# BUG-0023: selecting a popup item closes the popup itself, so screen_evidence's "after" falls back to the
# root window (different hwnd than "before"); arrival then reads whether the option's text reached the root.
POPUP = screen_hwnd(2, [("16:9 Aspect", FIELD), ("4:3 Aspect", (20, 50, 100, 20))])
ROOT_BEFORE = screen_hwnd(1, [("Resolution: Full HD", (20, 20, 150, 20))])
ROOT_AFTER = screen_hwnd(1, [("Resolution: 16:9 Aspect", (20, 20, 150, 20))])


def test_popup_item_click_whose_value_reached_the_root_and_fits_is_achieved():
    judge, asked = fit(True)
    assert ArrivalFitVerifier(judge)(popup_outcome(POPUP, ROOT_AFTER, ROOT_BEFORE)) is True
    assert asked[0][2]["target_part"] == "popup_closed_with_value"


def test_popup_item_click_whose_anchor_did_not_change_stays_unverified():
    judge, asked = fit(True)
    assert ArrivalFitVerifier(judge)(popup_outcome(POPUP, ROOT_BEFORE, ROOT_BEFORE)) is None
    assert asked == []


def test_popup_item_click_without_a_root_before_baseline_stays_unverified():
    judge, asked = fit(True)
    assert ArrivalFitVerifier(judge)(popup_outcome(POPUP, ROOT_AFTER, None)) is None
    assert asked == []


def test_popup_item_click_that_does_not_fit_stays_unverified_not_failed():
    judge, _ = fit(False)
    assert ArrivalFitVerifier(judge)(popup_outcome(POPUP, ROOT_AFTER, ROOT_BEFORE)) is None


def test_click_that_opens_an_owned_popup_asks_fit_with_the_popup_items():
    button = ObservedCandidate("c1", "click", "Free Aspect", "s", attributes={"rect": FIELD})
    item = ObservedCandidate("p1", "click", "16:9 Aspect", "s", attributes={"rect": (0, 0, 10, 10), "scope": "owned_popup",
                                                                              "scope_hwnd": 7})
    before = Observation("o1", "s", (button,), True)
    after = Observation("o2", "s", (button, item), True)
    judge, asked = fit(True)
    result = ArrivalFitVerifier(judge)(
        ActionOutcome("Open the resolution dropdown.", button, None, before, after, BEFORE, BEFORE, [], None, "achieved-1")
    )
    assert result is True
    assert asked[0][2]["opened_popup_items"] == ["16:9 Aspect"]


def test_click_with_no_landed_change_and_no_new_popup_stays_unverified():
    judge, asked = fit(True)
    assert ArrivalFitVerifier(judge)(outcome("click", BEFORE, BEFORE)) is None
    assert asked == []
