from finitact.action_adapter import Observation
from finitact.contracts import ObservedCandidate
from finitact.windows_inventory import filter_windows, observation_items, observe_window


def test_filter_windows_matches_title_or_process_case_insensitively():
    windows = [
        {"title": "#test | esp32 - Discord", "process": "Discord.exe"},
        {"title": "Windows PowerShell", "process": "powershell.exe"},
    ]
    assert filter_windows(windows, "discord") == windows[:1]
    assert filter_windows(windows, "POWERSHELL.EXE") == windows[1:]
    assert filter_windows(windows, None) == windows


def test_observe_window_reads_labels_once_without_input_and_closes_the_adapter():
    class Adapter:
        closed = False
        acted = False

        def observe(self):
            click = ObservedCandidate("a", "click", "finitact probe", "r1", attributes={"rect": (1, 2, 3, 4)})
            fill = ObservedCandidate("b", "fill", "finitact probe", "r1", attributes={"rect": (1, 2, 3, 4)})
            other = ObservedCandidate("c", "click", "#test", "r2", attributes={"rect": (5, 6, 7, 8)})
            return Observation("o", "s", (click, fill, other), True)

        def act(self, *args, **kwargs):
            Adapter.acted = True

        def close(self):
            Adapter.closed = True

    requests = []
    result = observe_window("screen:1:2", "PROBE", adapter_factory=lambda r: requests.append(r) or Adapter())
    assert result == {
        "total": 2,
        "matched": 1,
        "items": [{"label": "finitact probe", "rect": [1, 2, 3, 4]}],
        "truncated": False,
    }
    assert Adapter.closed and not Adapter.acted
    assert not requests[0].synthetic_input_allowed


def test_observation_items_truncates_long_lists():
    candidates = [
        ObservedCandidate(str(i), "click", f"row {i}", str(i), attributes={"rect": (i, 0, 1, 1)}) for i in range(250)
    ]
    result = observation_items(candidates, None)
    assert (result["total"], len(result["items"]), result["truncated"]) == (250, 200, True)


def test_contains_ignores_the_spaces_ocr_puts_between_characters_and_edge_shapes_are_dropped():
    spaced = ObservedCandidate("a", "click", "f i n i t a c t 比較 1 7 9 0", "r1", attributes={"rect": (1, 1, 1, 1)})
    edge = ObservedCandidate(
        "b", "click", "boxed_region_22x27", "r2", attributes={"rect": (2, 2, 1, 1), "source": "edge_contour"}
    )
    result = observation_items([spaced, edge], "finitact比較 1790")
    assert (result["total"], result["matched"]) == (1, 1)
    assert filter_windows([{"title": "", "process": "explorer.exe", "class_name": "Progman"}], "progman")


def test_text_on_the_focused_input_caret_line_is_marked_as_not_submitted():
    posted = ObservedCandidate("a", "click", "finitact 1", "r1", attributes={"rect": (454, 541, 218, 20)})
    typed = ObservedCandidate("b", "click", "finitact 2", "r2", attributes={"rect": (451, 671, 216, 22)})
    result = observation_items([posted, typed], "finitact", caret=(668, 672, 1, 21))
    assert [item.get("in_focused_input", False) for item in result["items"]] == [False, True]


def test_scrolled_out_items_are_marked_offscreen_not_in_the_focused_input():
    history = ObservedCandidate(
        "a", "click", "finitact 1", "r1", attributes={"rect": (0, 0, 1296, 728), "offscreen": "scrolled into view"}
    )
    typed = ObservedCandidate("b", "click", "finitact 2", "r2", attributes={"rect": (451, 671, 216, 22)})
    items = observation_items([history, typed], "finitact", caret=(668, 672, 1, 21))["items"]
    assert [(item.get("offscreen", False), item.get("in_focused_input", False)) for item in items] == [
        (True, False),
        (False, True),
    ]

def test_buttons_beside_the_input_box_holding_the_caret_are_not_marked():
    button = ObservedCandidate("a", "click", "options", "r1", attributes={"rect": (403, 667, 32, 32)})
    box = ObservedCandidate("b", "fill", "message #test", "r2", attributes={"rect": (455, 655, 620, 56)})
    typed = ObservedCandidate("c", "click", "finitact 2", "r3", attributes={"rect": (455, 672, 217, 21)})
    items = observation_items([button, box, typed], None, caret=(455, 672, 1, 21))["items"]
    assert [item.get("in_focused_input", False) for item in items] == [False, True, True]

def test_a_field_shows_its_value_and_text_inside_an_empty_field_is_its_placeholder():
    # E2E-03: the placeholder "jev@intelligence.ai" read like typed text and the agent tried to delete it.
    click = ObservedCandidate("a", "click", "Email", "r1", attributes={"rect": (1226, 625, 384, 41)})
    field = ObservedCandidate(
        "b", "fill", "Email", "r1", attributes={"rect": (1226, 625, 384, 41)}, binding={"field_value": ""}
    )
    hint = ObservedCandidate("c", "click", "jev@intelligence.ai", "r2", attributes={"rect": (1236, 634, 124, 23)})
    items = observation_items([click, field, hint], None, caret=(1236, 634, 1, 23))["items"]
    assert items == [
        {"label": "Email", "rect": [1226, 625, 384, 41], "in_focused_input": True, "value": ""},
        {"label": "jev@intelligence.ai", "rect": [1236, 634, 124, 23], "placeholder": True},
    ]


def test_uia_path_values_come_from_the_observation():
    field = ObservedCandidate("b", "fill", "Email", "r1", attributes={"rect": (1, 2, 3, 4)})
    button = ObservedCandidate("c", "click", "Continue", "r2", attributes={"rect": (1, 9, 3, 4)})
    items = observation_items([field, button], None, values={"b": "user@example.com", "c": "x"})["items"]
    assert [item.get("value") for item in items] == ["user@example.com", None]


def test_a_slider_shows_its_number():
    # BUG-0056: the volume slider was missing from observe_window altogether.
    slider = ObservedCandidate(
        "s", "set_range", "Volume", "r1", attributes={"rect": (1, 2, 3, 4)}, binding={"field_value": "47"}
    )
    assert observation_items([slider], None)["items"] == [{"label": "Volume", "rect": [1, 2, 3, 4], "value": "47"}]


def test_screenshot_is_the_frame_the_labels_came_from():
    from finitact.screen_grounded_adapter import RetainedScreen, WindowFrame

    frame = WindowFrame(1, 2, 2, 1, bytes([0, 0, 255, 255, 255, 0, 0, 255]))

    class Adapter:
        def observe(self):
            return Observation("o", "s", (ObservedCandidate("a", "click", "OK", "r", attributes={"rect": (0, 0, 1, 1)}),), True)

        def retain(self, _observation):
            return RetainedScreen((frame,))

        def close(self):
            pass

    view = observe_window("screen:1:2", None, adapter_factory=lambda _r: Adapter(), screenshot=True)
    assert view["screenshot_png"].startswith(b"\x89PNG")
    assert "screenshot_png" not in observe_window("screen:1:2", None, adapter_factory=lambda _r: Adapter())


def test_a_list_with_a_writable_value_is_not_a_field_that_holds_placeholders():
    listbox = ObservedCandidate(
        "a", "fill", "自動入力", "r1", attributes={"rect": (1317, 667, 186, 44), "control_type": "ControlType.List"}
    )
    item = ObservedCandidate("b", "click", "user@example.com", "r2", attributes={"rect": (1321, 671, 178, 36)})
    items = observation_items([listbox, item], None, values={"a": ""})["items"]
    assert items == [
        {"label": "自動入力", "rect": [1317, 667, 186, 44]},
        {"label": "user@example.com", "rect": [1321, 671, 178, 36]},
    ]


def test_items_in_an_owned_popup_are_marked_and_placed_in_target_window_coordinates():
    # E2E-03: the autofill suggestion came back at popup-relative [8, 12] with nothing saying a dropdown was open.
    field = ObservedCandidate("a", "click", "Email", "r1", attributes={"rect": (1226, 625, 384, 41)})
    suggestion = ObservedCandidate(
        "b", "click", "user@example.com", "r2",
        attributes={"rect": (8, 12, 178, 36), "scope": "owned_popup", "scope_hwnd": 77},
    )  # fmt: skip
    items = observation_items(
        [field, suggestion], None, caret=(1236, 634, 1, 23), root_hwnd=10, origins={10: (-8, -8), 77: (1313, 659)}
    )["items"]
    assert items[1] == {"label": "user@example.com", "rect": [1329, 679, 178, 36], "popup": True}


def test_uia_items_outside_the_target_window_are_marked_as_popup_without_moving():
    inside = ObservedCandidate("a", "click", "Continue", "r1", attributes={"rect": (1, 2, 3, 4), "scope_hwnd": 10})
    outside = ObservedCandidate("b", "click", "yuri", "r2", attributes={"rect": (5, 6, 7, 8), "scope_hwnd": 77})
    items = observation_items([inside, outside], None, root_hwnd=10)["items"]
    assert [item.get("popup", False) for item in items] == [False, True] and items[1]["rect"] == [5, 6, 7, 8]


def test_kept_observation_gives_item_refs_that_map_each_operation_to_its_candidate():
    class Adapter:
        def observe(self):
            click = ObservedCandidate("a", "click", "Email", "r1", attributes={"rect": (1, 2, 3, 4)})
            fill = ObservedCandidate("b", "fill", "Email", "r1", attributes={"rect": (1, 2, 3, 4)})
            other = ObservedCandidate("c", "click", "Send", "r2", attributes={"rect": (5, 6, 7, 8)})
            return Observation("o", "s", (click, fill, other), True)

        def retain(self, observation):
            return ("retained", observation.observation_id)

        def close(self):
            pass

    kept = []
    view = observe_window(
        "screen:1:2", "send", adapter_factory=lambda _r: Adapter(), keep=lambda *args: kept.append(args)
    )
    assert "observe_id" not in view and view["items"] == [{"ref": "2", "label": "Send", "rect": [5, 6, 7, 8]}]
    target, observation, retained, refs = kept[0]
    assert (target, retained) == ("screen:1:2", ("retained", "o"))
    assert refs == {"1": {"click": "a", "fill": "b"}, "2": {"click": "c"}} and observation.observation_id == "o"


def test_terminal_hosts_are_protected_by_default_and_the_env_list_replaces_them(monkeypatch):
    from finitact.windows_inventory import protected_reason

    monkeypatch.delenv("FINITACT_PROTECTED_PROCESSES", raising=False)
    assert protected_reason("WindowsTerminal.exe")
    assert protected_reason("CONHOST.EXE")
    assert protected_reason("msedge.exe") is None
    monkeypatch.setenv("FINITACT_PROTECTED_PROCESSES", "Code.exe")
    assert protected_reason("code.exe")
    assert protected_reason("WindowsTerminal.exe") is None
    monkeypatch.setenv("FINITACT_PROTECTED_PROCESSES", "")
    assert protected_reason("WindowsTerminal.exe") is None


def test_shell_peers_include_taskbar_but_not_explorer_folders_or_unrelated_windows():
    from finitact.windows_inventory import _is_shell_surface

    assert _is_shell_surface('explorer.exe', 'Shell_TrayWnd')
    assert _is_shell_surface('SearchHost.exe', 'Windows.UI.Core.CoreWindow')
    assert _is_shell_surface('StartMenuExperienceHost.exe', 'Windows.UI.Core.CoreWindow')
    assert not _is_shell_surface('explorer.exe', 'CabinetWClass')
    assert not _is_shell_surface('explorer.exe', 'Progman')
    assert not _is_shell_surface('other.exe', 'Shell_TrayWnd')
    assert not _is_shell_surface('other.exe', 'Windows.UI.Core.CoreWindow')
    assert not _is_shell_surface('SearchHost.exe', 'other')


def test_resolve_window_prefers_exact_title_and_refuses_ambiguity():
    import pytest

    from finitact.windows_inventory import resolve_window

    windows = [
        {"hwnd": 1, "pid": 10, "title": "Untitled - Notepad"},
        {"hwnd": 2, "pid": 20, "title": "Notepad"},
        {"hwnd": 3, "pid": 30, "title": "Notepad", "protected": True},
        {"hwnd": 4, "pid": 40, "title": "Blender"},
        {"hwnd": 5, "pid": 50, "title": "Blender 4.2"},
    ]
    assert resolve_window("Notepad", windows) == "window:2:20"
    assert resolve_window("untitled-notepad", windows) == "window:1:10"
    assert resolve_window("window:9:9", windows) == "window:9:9"
    with pytest.raises(ValueError, match="2 windows match"):
        resolve_window("Blend", windows)
    with pytest.raises(ValueError, match="no unprotected window"):
        resolve_window("Calculator", windows)


def test_resolve_window_takes_the_one_visible_window_over_minimized_namesakes():
    import pytest

    from finitact.windows_inventory import resolve_window

    windows = [
        {"hwnd": 1, "pid": 10, "title": "(Unsaved) - Blender", "minimized": True},
        {"hwnd": 2, "pid": 20, "title": "(Unsaved) - Blender", "minimized": False},
    ]
    assert resolve_window("(Unsaved) - Blender", windows) == "window:2:20"
    with pytest.raises(ValueError, match="2 windows match"):
        resolve_window("(Unsaved) - Blender", [{**w, "minimized": False} for w in windows])
