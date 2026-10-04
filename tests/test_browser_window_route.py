from finitact.browser_window_route import BrowserWindowRoute, find_singleton_tab


WINDOW = {"hwnd": 7, "pid": 31, "process": "chrome.exe", "title": "Calculator - Google Chrome", "minimized": False}
PAGE = {"targetId": "T1", "type": "page", "title": "Calculator", "url": "https://calculator.aws/#/addService"}
STATE = {"title": "Calculator", "url": PAGE["url"], "visibility": "visible"}


def probe(monkeypatch, *, windows=(WINDOW,), pages=(PAGE,), state=STATE, browser_pid=31):
    monkeypatch.setenv("BU_CDP_URL", "http://127.0.0.1:9222")
    monkeypatch.delenv("BU_CDP_WS", raising=False)
    calls = []

    def cdp(method, **kwargs):
        calls.append(method)
        if method == "SystemInfo.getProcessInfo":
            return {"processInfo": [{"type": "browser", "id": browser_pid}]}
        if method == "Target.getTargets":
            return {"targetInfos": pages}
        if method == "Browser.getWindowForTarget":
            return {"windowId": 8}
        if method == "Target.attachToTarget":
            return {"sessionId": "S1"}
        if method == "Runtime.evaluate":
            return {"result": {"value": state}}
        if method == "Target.detachFromTarget":
            return {}
        raise AssertionError(method)

    return find_singleton_tab("window:7:31", windows, cdp_call=cdp, window_probe=lambda: windows), calls


def test_singleton_probe_binds_local_process_window_and_visible_page(monkeypatch):
    route, calls = probe(monkeypatch)
    assert route == BrowserWindowRoute("T1", "https://calculator.aws")
    assert calls[-2:] == ["Runtime.evaluate", "Target.detachFromTarget"]


def test_probe_falls_back_for_ambiguous_or_unreachable_page(monkeypatch):
    assert probe(monkeypatch, pages=(PAGE, {**PAGE, "targetId": "T2"}))[0] is None
    assert probe(monkeypatch, browser_pid=99)[0] is None
    assert probe(monkeypatch, windows=(WINDOW, {**WINDOW, "hwnd": 9}))[0] is None
    assert probe(monkeypatch, state={**STATE, "visibility": "hidden"})[0] is None


def test_probe_never_touches_remote_cdp_endpoint(monkeypatch):
    monkeypatch.setenv("BU_CDP_URL", "http://example.com:9222")
    assert find_singleton_tab("window:7:31", (WINDOW,), cdp_call=lambda *a, **k: 1 / 0, window_probe=lambda: (WINDOW,)) is None


def test_probe_ignores_bubbles_of_the_target_window_but_not_other_owned_windows(monkeypatch):
    bubble = {**WINDOW, "hwnd": 9, "title": "Recent downloads", "popup_of": 7}
    assert probe(monkeypatch, windows=(bubble, WINDOW))[0] == BrowserWindowRoute("T1", "https://calculator.aws")
    assert probe(monkeypatch, windows=({**bubble, "popup_of": 5}, WINDOW))[0] is None
