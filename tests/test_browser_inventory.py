import base64

import pytest

from finitact.browser_inventory import observe_tab, page_view

PAGE = {
    "url": "https://calculator.aws/#/addService",
    "title": "Add service",
    "text": "Find Service\nNo results\nWe couldn't find a match",
    "actions": [
        {"id": "e1", "kind": "fill", "label": "Find Service", "value": "IPv4", "invalid": "Enter a service name"},
        {"id": "e2", "kind": "click", "label": "Configure Amazon SQS", "offscreen": "below (scrolled into view before input)"},
        {"id": "wait", "kind": "wait", "label": "Wait for the page to update"},
    ],
}


def test_page_view_lists_targets_with_states_and_matching_text_lines():
    view = page_view(PAGE, "no results")
    assert view["text"] == ["No results"] and view["items"] == []
    full = page_view(PAGE, None)
    assert full["items"][0] == {"kind": "fill", "label": "Find Service", "invalid": "Enter a service name", "value": "IPv4"}
    assert full["items"][1]["offscreen"].startswith("below") and len(full["items"]) == 2
    assert "screenshot_jpeg" not in full


def test_observe_tab_reads_the_kept_tab_once_and_detaches():
    opened = []

    class Tab:
        def __init__(self, url, tab_id=None):
            opened.append((url, tab_id))
            self.closed = False

        def call(self, method, **params):
            opened.append(method)

        def observe(self, screenshot=True):
            return {**PAGE, "screenshot": base64.b64encode(b"jpg").decode()} if screenshot else PAGE

        def close(self):
            opened.append("closed")

    view = observe_tab("T1", None, True, browser_factory=Tab)
    assert opened == [(None, "T1"), "Page.bringToFront", "closed"] and view["screenshot_jpeg"] == b"jpg"


def test_an_image_is_withheld_until_the_same_state_was_read_as_text():
    """Text first, enforced: 15 of 25 observations in E2E-02 asked for an image."""
    from finitact.browser_inventory import WITHHELD, ScreenshotGate

    class Tab:
        def __init__(self, url, tab_id=None):
            pass

        def call(self, method, **params):
            pass

        def observe(self, screenshot=True):
            return {**PAGE, "fingerprint": "f1", "screenshot": base64.b64encode(b"jpg").decode()} if screenshot else {**PAGE, "fingerprint": "f1"}

        def close(self):
            pass

    gate = ScreenshotGate()
    first = observe_tab("T1", None, True, browser_factory=Tab, gate=gate)
    assert first["screenshot"] == WITHHELD and "screenshot_jpeg" not in first
    second = observe_tab("T1", None, True, browser_factory=Tab, gate=gate)
    assert second["screenshot_jpeg"] == b"jpg"
    assert ScreenshotGate().allows("T2", "s", True, sparse=True)


def test_final_state_keeps_the_page_short_with_rejected_fields_first_class():
    from finitact.browser_inventory import final_state

    page = {**PAGE, "text": "\n".join(f"line {i}" for i in range(100))}
    state = final_state(page)
    assert state["url"] == PAGE["url"] and len(state["text"]) == 40
    assert state["invalid_fields"] == [{"label": "Find Service", "invalid": "Enter a service name", "value": "IPv4"}]
    assert "open_dialogs" not in state
    asking = final_state({**page, "dialogs": ["Export estimate\nCSV\nOK Cancel"]})
    assert list(asking)[:3] == ["url", "title", "open_dialogs"] and asking["open_dialogs"] == ["Export estimate\nCSV\nOK Cancel"]


def test_final_state_lists_current_form_values_once_per_field():
    """E2E-I28: a form page's first text lines are its header, so the outer agent observed again to read fields."""
    from finitact.browser_inventory import final_state

    page = {
        **PAGE,
        "actions": [
            *PAGE["actions"],
            {"kind": "fill", "label": "Description", "value": "web"},
            {"kind": "click", "label": "Open Description", "value": "web"},
            {"kind": "select", "label": "Region \u2192 Tokyo", "current_value": "Ohio"},
            {"kind": "select", "label": "Region \u2192 Osaka", "current_value": "Ohio"},
            {"kind": "click", "label": "Upfront", "checked": "true"},
            {"kind": "click", "label": "Monthly", "checked": "false"},
        ],
    }
    assert final_state(page)["fields"] == ["Description = web", "Region = Ohio", "[x] Upfront", "[ ] Monthly"]
    assert "fields" not in final_state({**PAGE, "actions": PAGE["actions"][1:]})


def test_downloads_of_the_run_are_reported_with_their_final_state():
    """E2E-I32: a confirmed export left no trace, so the outer agent downloaded the estimate twice."""
    from finitact.browser import track_downloads
    from finitact.browser_inventory import final_state

    events = [
        {"method": "Network.requestWillBeSent", "params": {}},
        {"method": "Browser.downloadWillBegin", "params": {"guid": "g1", "suggestedFilename": "My Estimate.csv"}},
        {"method": "Browser.downloadProgress", "params": {"guid": "g1", "state": "inProgress"}},
        {"method": "Browser.downloadProgress", "params": {"guid": "g1", "state": "completed"}},
        {"method": "Browser.downloadProgress", "params": {"guid": "other", "state": "completed"}},
    ]
    downloads = track_downloads(events, {})
    assert list(downloads.values()) == [{"file": "My Estimate.csv", "state": "completed"}]
    assert final_state(PAGE, list(downloads.values()))["downloads"] == [{"file": "My Estimate.csv", "state": "completed"}]
    assert "downloads" not in final_state(PAGE)


@pytest.mark.parametrize("tab_id", [None, "existing-tab"])
def test_download_watch_uses_the_owned_tab_session(monkeypatch, tab_id):
    from finitact import browser as module

    calls = []

    def cdp(method, session_id=None, **params):
        calls.append((method, session_id))
        return {"targetId": "new-tab"} if method == "Target.createTarget" else {"sessionId": "owned-session"}

    monkeypatch.setattr(module, "start_harness", lambda: None)
    monkeypatch.setattr(module, "cdp", cdp)
    monkeypatch.setattr(module, "drain_events", lambda: [])
    monkeypatch.setattr(module.Browser, "evaluate", lambda self, expression: "complete")
    monkeypatch.setattr(module, "KEPT", {})
    monkeypatch.setattr(module, "SESSIONS", {})
    browser = module.Browser("https://example.test", tab_id=tab_id)
    browser.close()

    watch = calls.index(("Browser.setDownloadBehavior", "owned-session"))
    assert calls.index(("Target.attachToTarget", None)) < watch
    # ADR-0047: a kept tab keeps its session for the next run instead of detaching.
    assert ("Target.detachFromTarget", None) not in calls
    assert module.KEPT == ({"existing-tab": "owned-session"} if tab_id else {})
    assert tab_id or ("Target.closeTarget", None) in calls


def _local_mode(monkeypatch, module, *, alive=False, answers=True, kind="local", attachable=False, running=None):
    events = []
    monkeypatch.delenv("BU_CDP_URL", raising=False)
    monkeypatch.delenv("BU_CDP_WS", raising=False)
    monkeypatch.setattr(module, "daemon_alive", lambda: alive)
    monkeypatch.setattr(module, "_daemon_answers_cdp", lambda: answers)
    monkeypatch.setattr(module, "daemon_browser_kind", lambda: kind)
    monkeypatch.setattr(module, "_reset_daemon", lambda: events.append("reset"))
    monkeypatch.setattr(module, "user_browser_attachable", lambda: attachable)
    monkeypatch.setattr(module, "dedicated_endpoint", lambda: running)
    monkeypatch.setattr(module, "launch_dedicated", lambda: events.append("launch") or "http://127.0.0.1:50001")
    monkeypatch.setattr(module, "ensure_daemon", lambda **kw: events.append(("ensure", kw)))
    return events


def test_start_harness_launches_the_finitact_profile_without_a_cdp_browser(monkeypatch):
    from finitact import browser as module

    events = _local_mode(monkeypatch, module)
    assert module.start_harness(startup_wait_s=7.0) is True
    assert events == ["launch", ("ensure", {"wait": 7.0, "env": {"BU_CDP_URL": "http://127.0.0.1:50001"}})]


def test_start_harness_reuses_a_running_finitact_profile(monkeypatch):
    from finitact import browser as module

    events = _local_mode(monkeypatch, module, running="http://127.0.0.1:50002")
    assert module.start_harness(startup_wait_s=7.0) is True
    assert events == [("ensure", {"wait": 7.0, "env": {"BU_CDP_URL": "http://127.0.0.1:50002"}})]


def test_start_harness_prefers_a_user_browser_that_listens_for_cdp(monkeypatch):
    from finitact import browser as module

    events = _local_mode(monkeypatch, module, attachable=True, running="http://127.0.0.1:50002")
    assert module.start_harness() is False
    assert events == [("ensure", {})]


def test_start_harness_defers_to_a_live_daemon(monkeypatch):
    from finitact import browser as module

    events = _local_mode(monkeypatch, module, alive=True)
    assert module.start_harness() is False and not events
    events = _local_mode(monkeypatch, module, alive=True, kind="cdp")
    assert module.start_harness() is True and not events


def test_start_harness_replaces_a_daemon_whose_browser_closed(monkeypatch):
    from finitact import browser as module

    events = _local_mode(monkeypatch, module, alive=True, answers=False, kind="cdp")
    assert module.start_harness(startup_wait_s=7.0) is True
    assert events[:2] == ["reset", "launch"]


def test_start_harness_fails_fast_on_unreachable_cdp_url(monkeypatch):
    import socket
    import time

    from finitact import browser as module

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    started = []
    monkeypatch.setenv("BU_CDP_URL", f"http://127.0.0.1:{port}")
    monkeypatch.setattr(module, "daemon_alive", lambda: False)
    monkeypatch.setattr(module, "ensure_daemon", lambda **kw: started.append(kw))
    begun = time.monotonic()
    with pytest.raises(RuntimeError, match="chrome-not-running: BU_CDP_URL"):
        module.start_harness()
    assert time.monotonic() - begun < 3 and not started


def test_start_harness_bounds_a_stuck_cdp_daemon_and_restarts_it_once(monkeypatch):
    from finitact import browser as module

    started, resets = [], []

    def ensure_daemon(**kw):
        started.append(kw)
        if len(started) == 1:
            raise RuntimeError("listening on 127.0.0.1:52705 (name=default, remote=local)")

    monkeypatch.setenv("BU_CDP_URL", "http://127.0.0.1:9222")
    monkeypatch.setattr(module, "daemon_alive", lambda: True)
    monkeypatch.setattr(module, "_daemon_answers_cdp", lambda: True)
    monkeypatch.setattr(module, "ensure_daemon", ensure_daemon)
    monkeypatch.setattr(module, "_reset_daemon", lambda: resets.append(True))
    module.start_harness(startup_wait_s=7.0)
    assert started == [{"wait": 7.0, "env": None}] * 2 and resets == [True]


def test_start_harness_stops_a_stale_cdp_daemon_before_the_harness_sees_it(monkeypatch):
    from finitact import browser as module

    events = []
    monkeypatch.setenv("BU_CDP_URL", "http://127.0.0.1:9222")
    monkeypatch.setattr(module, "daemon_alive", lambda: True)
    monkeypatch.setattr(module, "_daemon_answers_cdp", lambda: False)
    monkeypatch.setattr(module, "_reset_daemon", lambda: events.append("reset"))
    monkeypatch.setattr(module, "ensure_daemon", lambda **kw: events.append("ensure"))
    module.start_harness()
    assert events == ["reset", "ensure"]


def _fake_daemon(monkeypatch, tmp_path, module, *, alive_polls, unlink_error=None):
    port, pid = tmp_path / "bu.port", tmp_path / "bu.pid"
    port.write_text("{}")
    pid.write_text("4242")
    killed, polls = [], []
    monkeypatch.setattr(module.harness_ipc, "port_path", lambda name: port)
    monkeypatch.setattr(module.harness_ipc, "pid_path", lambda name: pid)
    monkeypatch.setattr(module.harness_ipc, "identify", lambda name, timeout: 4242)
    monkeypatch.setattr(module.harness_ipc, "connect", lambda name, timeout: (_ for _ in ()).throw(ConnectionRefusedError()))
    monkeypatch.setattr(module, "_pid_running", lambda p: polls.append(p) or len(polls) <= alive_polls)
    monkeypatch.setattr(module.os, "kill", lambda p, sig: killed.append((p, sig)))
    if unlink_error:
        monkeypatch.setattr(type(port), "unlink", lambda self, missing_ok=False: (_ for _ in ()).throw(unlink_error))
    return port, pid, killed


def test_reset_daemon_waits_for_exit_then_removes_the_endpoint(monkeypatch, tmp_path):
    from finitact import browser as module

    port, pid, killed = _fake_daemon(monkeypatch, tmp_path, module, alive_polls=2)
    module._reset_daemon(exit_wait_s=1.0)
    assert not killed and not port.exists() and not pid.exists()


def test_reset_daemon_kills_a_daemon_that_outlives_shutdown(monkeypatch, tmp_path):
    from finitact import browser as module

    _, _, killed = _fake_daemon(monkeypatch, tmp_path, module, alive_polls=10**6)
    module._reset_daemon(exit_wait_s=0.2)
    assert killed == [(4242, 9)]


def test_reset_daemon_names_an_endpoint_that_stays_undeletable(monkeypatch, tmp_path):
    from finitact import browser as module

    _fake_daemon(monkeypatch, tmp_path, module, alive_polls=0, unlink_error=PermissionError(13, "denied"))
    with pytest.raises(RuntimeError, match="stayed undeletable"):
        module._reset_daemon(settle_s=0.3)


def test_start_harness_does_not_restart_on_a_permission_prompt(monkeypatch):
    from finitact import browser as module

    monkeypatch.setenv("BU_CDP_URL", "http://127.0.0.1:9222")
    monkeypatch.setattr(module, "daemon_alive", lambda: True)
    monkeypatch.setattr(module, "_daemon_answers_cdp", lambda: True)

    def ensure_daemon(**kw):
        raise RuntimeError("permission-blocked: Chrome did not approve the connection")

    monkeypatch.setattr(module, "ensure_daemon", ensure_daemon)
    monkeypatch.setattr(module, "_reset_daemon", lambda: pytest.fail("reset on a permission prompt"))
    with pytest.raises(RuntimeError, match="permission-blocked"):
        module.start_harness()


def test_parts_run_browser_cannot_enter_are_counted_in_final_state_and_view():
    from finitact.browser_inventory import final_state

    page = {**PAGE, "unsupported": {"frames": 1, "open_shadow_roots": 0, "popup_links": 2, "canvases": 3}}
    assert final_state(page)["out_of_reach"] == {"frames": 1, "popup_links": 2}
    assert page_view(page, None)["out_of_reach"] == {"frames": 1, "popup_links": 2}
    assert "out_of_reach" not in final_state({**PAGE, "unsupported": {"canvases": 3}})


def test_screen_target_is_the_single_browser_window_titled_by_the_page():
    from finitact.browser_inventory import screen_target

    chrome = {"hwnd": 11, "pid": 5, "process": "chrome.exe", "title": "Add service - Google Chrome"}
    editor = {"hwnd": 12, "pid": 6, "process": "notepad.exe", "title": "Add service - Notepad"}
    assert screen_target("Add service", [editor, chrome]) == "window:11:5"
    assert screen_target("Add service", [chrome, {**chrome, "hwnd": 13, "process": "msedge.exe"}]) is None
    assert screen_target("Other tab", [chrome]) is None
    assert screen_target("", [chrome]) is None


def test_show_tab_brings_the_kept_tab_forward_and_detaches():
    from finitact.browser_inventory import show_tab

    calls = []

    class Tab:
        def __init__(self, url, tab_id):
            calls.append(("open", tab_id))

        def call(self, method):
            calls.append(method)

        def close(self):
            calls.append("close")

    show_tab("T1", browser_factory=Tab, settle_s=0)
    assert calls == [("open", "T1"), "Page.bringToFront", "close"]
