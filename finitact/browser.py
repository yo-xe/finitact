"""Observed actions through Browser Harness; one CDP session, no per-step subprocess."""

import hashlib
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from browser_harness import _ipc as harness_ipc
from browser_harness.admin import NAME as HARNESS_NAME
from browser_harness.admin import _is_daemon_process, _pid_number, daemon_alive, ensure_daemon
# Importing the daemon module also loads the harness .env, so BU_CDP_URL is seen as the daemon sees it.
from browser_harness.daemon import supported_browser_running
from browser_harness.helpers import _send, cdp, drain_events

# Atomically read visible content and controls, preserving actual DOM node identity.
READ_STATE = Path(__file__).with_name("snapshot.js").read_text(encoding="utf-8")
MARKER = f"(() => {{ const state={READ_STATE}; return state?.marker ?? null; }})()"

class StalePage(ValueError):
    """A decision no longer refers to the observed page."""


class MutationUncertain(RuntimeError):
    """Browser input may have started, so the logical mutation must not be retried."""


class DialogOpened(Exception):
    """A JavaScript dialog opened while a CDP call was waiting on the blocked renderer."""

    def __init__(self, opened, inflight=None):
        super().__init__(opened[1].get("type"))
        self.gen, self.dialog = opened
        # (result box, done event) of the call still waiting in its thread; it returns once the dialog closes.
        self.inflight = inflight


class SessionDialog:
    """ADR-0047: dialog state of one of our CDP sessions.

    The daemon keeps one dialog for all tabs and any tab's close clears it, so each session keeps its own. ``gen``
    counts dialogs opened on it, telling apart two dialogs with the same text. ``pending`` is an input a dialog
    interrupted: it is delivered only once the dialog is answered and its remaining steps have been sent.
    """

    def __init__(self, target):
        self.target, self.gen, self.dialog, self.answered, self.pending = target, 0, None, None, None


SESSIONS = {}
# ADR-0047: only the session that had Page enabled when a dialog opened can answer it, so a kept tab keeps its
# session attached (target id -> session id) for the next run or observe_browser, dialog or not.
KEPT = {}
BACKLOG = []
BACKLOG_MAX = 1000
_EVENTS_LOCK = threading.Lock()
DIALOG_POLL_S = 0.1
# The watcher, not the IPC socket, bounds a call: a call blocked by a dialog answers only after the dialog closes.
CALL_TIMEOUT_S = 5.0
INFLIGHT_TIMEOUT_S = 900.0
DIALOG_CHOICES = {
    "alert": [("accept", "OK")],
    "confirm": [("accept", "OK"), ("dismiss", "Cancel")],
    "beforeunload": [("accept", "Leave page"), ("dismiss", "Stay on page")],
    "prompt": [("dismiss", "Cancel")],
}


def session_state(session, target=None):
    state = SESSIONS.get(session)
    if state is None:
        state = SESSIONS[session] = SessionDialog(target)
    elif target is not None:
        state.target = target
    return state


def pull_events():
    """Fold the daemon's dialog events into our sessions; keep the rest for ``drain``."""
    events = drain_events()
    with _EVENTS_LOCK:
        for event in events:
            method = event.get("method")
            if method not in ("Page.javascriptDialogOpening", "Page.javascriptDialogClosed"):
                BACKLOG.append(event)
                continue
            state = SESSIONS.get(event.get("session_id"))
            if state is None and event.get("session_id") is None:
                frame = (event.get("params") or {}).get("frameId")
                state = next((s for s in SESSIONS.values() if s.target is not None and s.target == frame), None)
            if state is None:
                continue
            if method == "Page.javascriptDialogOpening":
                state.gen += 1
                state.dialog = event.get("params") or {}
            else:
                state.dialog = state.answered = None
        del BACKLOG[:-BACKLOG_MAX]


def drain():
    """Events other than dialogs since the last drain (downloads, drag interception)."""
    pull_events()
    with _EVENTS_LOCK:
        events = BACKLOG[:]
        BACKLOG.clear()
    return events


def current_dialog(session, target):
    """(generation, dialog) open on this session's tab, or None."""
    if session is None or target is None:
        return None
    pull_events()
    state = session_state(session, target)
    if state.dialog is None:
        # The opening event can fall out of the daemon's 500-event buffer while nothing drains it between runs.
        # Its single dialog is used only when it is this tab's main frame (frameId == target, ADR-0047 probe).
        meta = _send({"meta": "pending_dialog"}).get("dialog")
        if meta and meta.get("frameId") == target and meta != state.answered:
            state.gen += 1
            state.dialog = meta
    return (state.gen, state.dialog) if state.dialog is not None else None


def watch_dialog(call, session, target):
    """Run one CDP call; raise DialogOpened as soon as a dialog on this tab blocks it instead of waiting it out."""
    if session is None or target is None:
        return call()
    box, done = {}, threading.Event()

    def run():
        try:
            box["value"] = call()
        except BaseException as exc:  # noqa: BLE001 - handed to the waiting caller
            box["error"] = exc
        finally:
            done.set()

    threading.Thread(target=run, daemon=True).start()
    deadline = time.monotonic() + CALL_TIMEOUT_S
    while not done.wait(DIALOG_POLL_S):
        if (opened := current_dialog(session, target)) is not None:
            raise DialogOpened(opened, (box, done))
        if time.monotonic() >= deadline:
            raise TimeoutError(f"CDP call timed out after {CALL_TIMEOUT_S:g}s")
    if "error" in box:
        raise box["error"]
    return box["value"]


def dialog_page(opened, url, title):
    """A dialog as the whole observation: the page behind it cannot be read until the dialog is answered."""
    gen, dialog = opened
    kind = dialog.get("type") or "alert"
    message = str(dialog.get("message") or "")
    text = f"{kind} dialog: {message}".strip()
    actions = [
        {"id": f"dialog_{choice}", "kind": "click", "label": label, "role": "button", "dialog": choice}
        for choice, label in DIALOG_CHOICES.get(kind, DIALOG_CHOICES["alert"])
    ]
    if kind == "prompt":
        # Filling a prompt also presses OK, unlike an ordinary field that is submitted by a later choice.
        actions.insert(0, {"id": "dialog_prompt", "kind": "fill", "label": "Type the answer and press OK (submits at once)",
                           "role": "textbox", "value": dialog.get("defaultPrompt") or "", "dialog": "accept"})
    page = {
        "url": url or dialog.get("url"), "title": title, "text": text, "dialogs": [text], "actions": actions,
        "scroll": {"y": 0, "height": 0}, "unsupported": {}, "guards": {}, "js_dialog": dialog, "dialog_gen": gen,
        "marker": ["dialog", gen, kind, message], "page_key": ["dialog", kind, message, "", "", "", []],
    }
    # Two dialogs with the same text are two observations; what they mean is the same.
    page["fingerprint"] = hashlib.sha256(f"{fingerprint(page)}\0{gen}".encode()).hexdigest()
    page["semantic_fingerprint"] = semantic_fingerprint(page)
    return page


def semantic_page_key(page_key):
    """Drop ephemeral DOM identities while retaining document and form state."""
    return [*page_key[:6], [values[1:] for values in page_key[6]]]


def semantic_guard(guard):
    """Drop only the target's ephemeral DOM identity from a guard."""
    return guard[1:] if guard else guard


def track_downloads(events, downloads):
    """Fold Browser.download* events into ``downloads`` (guid -> {"file", "state"})."""
    for event in events:
        params = event.get("params") or {}
        if event.get("method") == "Browser.downloadWillBegin":
            downloads.setdefault(params.get("guid"), {"file": params.get("suggestedFilename"), "state": "inProgress"})
        elif event.get("method") == "Browser.downloadProgress" and params.get("guid") in downloads:
            downloads[params["guid"]]["state"] = params.get("state", "inProgress")
    return downloads


def start_harness(probe_timeout_s=2.0, startup_wait_s=15.0):
    """Start the harness daemon, failing at once when no browser can answer CDP."""
    # BUG-0042: without a browser the harness launched one and retried under 60s windows, costing about 120 seconds.
    if not daemon_alive():
        if url := os.environ.get("BU_CDP_URL"):
            try:
                urllib.request.urlopen(f"{url.rstrip('/')}/json/version", timeout=probe_timeout_s).close()
            except urllib.error.HTTPError:
                pass  # reachable; the daemon reports permission or discovery states itself
            except OSError as exc:
                raise RuntimeError(
                    f"chrome-not-running: BU_CDP_URL={url} unreachable ({exc}) -- start Chrome with --remote-debugging-port, then retry"
                ) from exc
        elif not os.environ.get("BU_CDP_WS") and not supported_browser_running():
            raise RuntimeError("chrome-not-running: no supported Chromium-family browser is running -- start Chrome, then retry")
    if not (os.environ.get("BU_CDP_URL") or os.environ.get("BU_CDP_WS")):
        ensure_daemon()  # a local Chrome's approval popup may need the user, so its wait stays unbounded
        return
    # BUG-0043: after the CDP Chrome restarts, a daemon can listen yet never answer ping, or the old endpoint file
    # refuses deletion (WinError 5); the harness then waited its full 60 seconds. A healthy start takes under 5.
    # A stale daemon is stopped here so the harness never reaches restart_daemon, which on Windows terminates the
    # daemon mid-shutdown (os.kill(pid, 0)) and lets PermissionError from the endpoint unlink escape.
    if daemon_alive() and not _daemon_answers_cdp():
        _reset_daemon()
    try:
        ensure_daemon(wait=startup_wait_s)
    except (RuntimeError, OSError) as exc:
        if str(exc).startswith(("chrome-not-running", "permission-blocked", "remote-debugging-setup")):
            raise
        _reset_daemon()
        ensure_daemon(wait=startup_wait_s)


def _daemon_answers_cdp():
    for attempt in range(2):
        try:
            sock, token = harness_ipc.connect(HARNESS_NAME, timeout=3.0)
            try:
                if "result" in harness_ipc.request(sock, token, {"method": "Target.getTargets", "params": {}}):
                    return True
            finally:
                sock.close()
        except (OSError, ValueError):
            pass
        if not attempt:
            time.sleep(0.5)
    return False


def _pid_running(pid):
    if sys.platform != "win32":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
    if not handle:
        return False
    try:
        return kernel32.WaitForSingleObject(handle, 0) == 0x102  # WAIT_TIMEOUT
    finally:
        kernel32.CloseHandle(handle)


def _reset_daemon(exit_wait_s=3.0, settle_s=10.0):
    """Stop the harness daemon and remove its endpoint files, raising when a file stays undeletable."""
    deadline = time.monotonic() + settle_s
    pid = harness_ipc.identify(HARNESS_NAME, timeout=2.0)
    if pid is None:
        pid = _pid_number(harness_ipc.pid_path(HARNESS_NAME))
        if pid and not _is_daemon_process(pid):  # a dead daemon's number may belong to another process by now
            pid = None
    try:
        sock, token = harness_ipc.connect(HARNESS_NAME, timeout=2.0)
        try:
            harness_ipc.request(sock, token, {"meta": "shutdown"})
        finally:
            sock.close()
    except (OSError, ValueError):
        pass
    exit_deadline = time.monotonic() + exit_wait_s
    while pid and _pid_running(pid) and time.monotonic() < exit_deadline:
        time.sleep(0.1)
    if pid and _pid_running(pid):
        try:
            os.kill(pid, 9)
        except OSError:
            pass
    # The dying daemon's own unlink can leave the file delete-pending (WinError 5) until its handles close.
    for path in (harness_ipc.port_path(HARNESS_NAME), harness_ipc.pid_path(HARNESS_NAME)):
        while True:
            try:
                path.unlink(missing_ok=True)
                break
            except PermissionError as exc:
                if time.monotonic() > deadline:
                    raise RuntimeError(f"daemon endpoint {path} stayed undeletable for {settle_s}s: {exc}") from exc
                time.sleep(0.2)


class Browser:
    def __init__(self, url, width=1120, height=780, mobile=False, *, tab_id=None, keep=False, drag=False):
        start_harness()
        # ADR-0046: drag sources and drop areas are read only for a run that asked for drag.
        self.drag = drag
        # E2E-I15: a run may continue in a tab an earlier run kept; that tab is never navigated, resized or closed.
        self.keep = keep or tab_id is not None
        if tab_id is not None:
            self.target = tab_id
            if self._reuse(KEPT.pop(tab_id, None)):
                return
            self._attach()
            self.call("Emulation.setFocusEmulationEnabled", enabled=True)
            return
        self.target = cdp("Target.createTarget", url="about:blank", background=True)["targetId"]
        self._attach()
        self.call(
            "Emulation.setDeviceMetricsOverride",
            width=width, height=height, deviceScaleFactor=1, mobile=mobile,
        )
        # Keep rAF/menus rendering in an owned background tab, without activating the user's Chrome tab.
        self.call("Emulation.setFocusEmulationEnabled", enabled=True)
        self.call("Page.navigate", url=url)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if self.evaluate("document.readyState") == "complete":
                break
            time.sleep(0.02)

    def _attach(self):
        self.session = cdp("Target.attachToTarget", targetId=self.target, flatten=True)["sessionId"]
        session_state(self.session, self.target)
        self._watch_downloads()
        self._enable_page()

    def _reuse(self, session):
        """Continue on the session an earlier run kept; False when it no longer answers."""
        if session is None:
            return False
        self.session = session
        session_state(session, self.target)
        self._watch_downloads()
        if self.dialog() is not None:
            return True
        try:
            watch_dialog(lambda: self.call("Emulation.setFocusEmulationEnabled", enabled=True), session, self.target)
        except DialogOpened:
            return True
        except Exception:  # noqa: BLE001 - a detached or crashed session; a fresh attach decides whether the tab lives
            SESSIONS.pop(session, None)
            return False
        return True

    def _enable_page(self):
        try:
            self.call("Page.enable")
        except TimeoutError as exc:
            # A dialog opened before this attach blocks Page.enable, and no later session can answer it (ADR-0047).
            raise RuntimeError(
                "tab-blocked: the tab did not answer Page.enable, most likely a JavaScript dialog opened outside "
                "Finitact; answer it in Chrome or close the tab"
            ) from exc

    def dialog(self):
        return current_dialog(getattr(self, "session", None), getattr(self, "target", None))

    def _dialog_page(self, opened):
        info = cdp("Target.getTargetInfo", targetId=self.target)["targetInfo"]
        return dialog_page(opened, info.get("url"), info.get("title"))

    def _watch_downloads(self):
        # E2E-I32: a confirmed download leaves no trace on the page, so the outer agent exported the estimate again.
        self.download_state = None
        try:
            cdp("Browser.setDownloadBehavior", session_id=self.session, behavior="default", eventsEnabled=True)
            drain()
        except Exception:  # noqa: BLE001 - a browser without download events still runs; it just reports none
            return
        self.download_state = {}

    def downloads(self, settle_s=3.0):
        """Downloads begun since this tab was opened or attached, waiting briefly for those still in progress."""
        if self.download_state is None:
            return []
        deadline = time.monotonic() + settle_s
        while True:
            track_downloads(drain(), self.download_state)
            pending = any(item["state"] == "inProgress" for item in self.download_state.values())
            if not pending or time.monotonic() >= deadline:
                return list(self.download_state.values())
            time.sleep(0.1)

    def call(self, method, **params):
        return cdp(method, session_id=self.session, **params)

    def _watched(self, method, **params):
        return watch_dialog(
            lambda: cdp(method, session_id=self.session, _response_timeout=INFLIGHT_TIMEOUT_S, **params),
            self.session, getattr(self, "target", None),
        )

    def evaluate(self, expression):
        response = self._watched("Runtime.evaluate", expression=expression, returnByValue=True)
        if response.get("exceptionDetails"):
            raise StalePage("Document changed during evaluation")
        return response.get("result", {}).get("value")

    def observe(self, screenshot=True):
        try:
            if (opened := self.dialog()) is None and session_state(self.session).pending is not None:
                # The dialog was answered outside this run (or by the page); finish the input it interrupted first.
                opened = resume_pending(self.session, self.target).get("opened")
            if opened is not None:
                return self._dialog_page(opened)
            return self._observe(screenshot)
        except DialogOpened as exc:
            return self._dialog_page((exc.gen, exc.dialog))

    def _observe(self, screenshot):
        if getattr(self, "after_input", None):
            action, self.after_input = self.after_input, None
            # This is read-only and happens after execution was logged, even if navigation interrupts it.
            try:
                self._watched(
                    "Runtime.evaluate",
                    expression="""(action => new Promise(resolve => {
                      const field=window.__jevFast?.nodes.get(action.node);
                      const autocomplete=action.kind==='fill' && field?.getAttribute('role')==='combobox';
                      let frames=0, stopped=false;
                      const finish=()=>{stopped=true;resolve()};
                      setTimeout(finish,autocomplete ? 200 : 50);
                      const ready=()=>{
                        if (stopped) return;
                        const ids=(field?.getAttribute('aria-controls')||field?.getAttribute('aria-owns')||'')
                          .split(/\\s+/).filter(Boolean);
                        const roots=ids.length ? ids.map(id=>document.getElementById(id)).filter(Boolean) : [document];
                        const options=roots.flatMap(root=>[...root.querySelectorAll('[role="option"]')]);
                        if (++frames>=2 && (!autocomplete || options.some(e=>{
                          const r=e.getBoundingClientRect();
                          return r.width && r.height && r.bottom>0 && r.top<innerHeight &&
                            e.checkVisibility({checkOpacity:true,checkVisibilityCSS:true});
                        }))) finish();
                        else requestAnimationFrame(ready);
                      };
                      requestAnimationFrame(ready);
                    }))(""" + json.dumps(action) + ")",
                    awaitPromise=True,
                    returnByValue=True,
                )
            except (RuntimeError, TimeoutError):
                pass
        for attempt in range(10):
            try:
                return browser_operation(
                    {"operation": "observe", "session": self.session, "target": self.target,
                     "screenshot": screenshot, "drag": getattr(self, "drag", False)}
                )
            except StalePage:
                if attempt == 9:
                    raise
                time.sleep(0.02)
        raise StalePage("Page did not settle")

    def fresh(self, page, action=None):
        opened = self.dialog()
        if "js_dialog" in page or opened:
            # By generation: an answer chosen for one dialog never goes to a later one with the same text.
            return opened is not None and opened[0] == page.get("dialog_gen")
        try:
            return self._fresh(page, action)
        except DialogOpened:
            return False

    def _fresh(self, page, action):
        if action is not None and action["kind"] in {"click", "select"}:
            node = action["node"]
            if type(node) is not int:
                return False
            current = self.evaluate(
                "(() => { const c=window.__jevFast; "
                f"return c ? [c.pageKey(),c.guardNode({node})] : null; }})()"
            )
            expected_guard = page["guards"].get(str(node))
            if current == [page["page_key"], expected_guard]:
                return True

            # Frameworks may replace an unchanged control between prediction and input.
            # Rebind only when document/form state is unchanged and exactly one current
            # action has the same operation, meaning, and nearby context.
            current_page = browser_operation(
                {"operation": "observe", "session": self.session, "screenshot": False, "drag": getattr(self, "drag", False)}
            )
            if semantic_page_key(current_page["page_key"]) != semantic_page_key(page["page_key"]):
                return False
            expected_action = {k: v for k, v in action.items() if k not in {"id", "node", "rect"}}
            matches = []
            for candidate in current_page["actions"]:
                comparable = {k: v for k, v in candidate.items() if k not in {"id", "node", "rect"}}
                if comparable != expected_action:
                    continue
                guard = current_page["guards"].get(str(candidate.get("node")))
                if semantic_guard(guard) == semantic_guard(expected_guard):
                    matches.append(candidate)
            nodes = {candidate["node"] for candidate in matches}
            if len(nodes) != 1:
                return False
            rebound = nodes.pop()
            action["node"] = rebound
            page["guards"][str(rebound)] = current_page["guards"][str(rebound)]
            return True
        return self.evaluate(MARKER) == page["marker"]

    def act(self, action, page, text=None):
        if not self.fresh(page, action):
            raise StalePage("Page changed since this decision. Observe again.")
        if action["kind"] == "wait":
            time.sleep(0.1)
        result = browser_operation(
            {"operation": "act", "session": self.session, "target": getattr(self, "target", None),
             "action": action, "text": text}
        )
        self.after_input = action if action["kind"] != "wait" and "dialog" not in result else None
        # A drag reads the event queue to see the browser take over the drag; what it did not use is handed back here.
        events = result.pop("events", [])
        if self.download_state is not None:
            # The daemon keeps only the last 500 events; network traffic of a long run would push a download out.
            track_downloads([*events, *drain()], self.download_state)
        return result

    def close(self):
        if self.target and self.keep:
            # Detaching would lose the only session able to answer a dialog that opens after this run (ADR-0047).
            KEPT[self.target] = self.session
            self.target = None
        elif self.target:
            cdp("Target.closeTarget", targetId=self.target)
            SESSIONS.pop(self.session, None)
            self.target = None


def fingerprint(state):
    content = {k: state[k] for k in ("url", "text", "actions", "scroll")}
    content["unsupported"] = state.get("unsupported", {})
    if state.get("drag_sources") or state.get("drop_targets"):
        content["drag"] = [state.get("drag_sources"), state.get("drop_targets")]
    return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()


def semantic_fingerprint(state):
    actions = [
        {k: v for k, v in action.items() if k not in {"id", "node", "rect"}}
        for action in state["actions"]
    ]
    content = {
        "url": state["url"],
        "text": state["text"],
        "actions": actions,
        "scroll": state["scroll"],
        "unsupported": state.get("unsupported", {}),
    }
    if state.get("drag_sources") or state.get("drop_targets"):
        content["drag"] = [
            [{k: v for k, v in item.items() if k not in {"node", "rect"}} for item in state.get(key) or []]
            for key in ("drag_sources", "drop_targets")
        ]
    return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()


def _calls(session, target):
    """CDP helpers for one session; with a target, a call blocked by this tab's dialog raises DialogOpened."""

    def call(method, **params):
        return cdp(method, session_id=session, **params)

    def blocking(method, **params):
        if session is None or target is None:
            return call(method, **params)
        return watch_dialog(
            lambda: cdp(method, session_id=session, _response_timeout=INFLIGHT_TIMEOUT_S, **params), session, target
        )

    def evaluate(expression, *, mutation=False):
        try:
            result = blocking("Runtime.evaluate", expression=expression, returnByValue=True)
        except DialogOpened:
            raise
        except Exception as exc:
            if mutation:
                raise MutationUncertain("Browser mutation result is unknown; inspect before retrying.") from exc
            raise
        if result.get("exceptionDetails"):
            if mutation:
                raise MutationUncertain("Browser mutation result is unknown; inspect before retrying.")
            raise StalePage("Document changed during evaluation")
        return result.get("result", {}).get("value")

    def mutation_call(method, **params):
        try:
            return blocking(method, **params)
        except DialogOpened:
            raise
        except Exception as exc:
            raise MutationUncertain("Browser mutation result is unknown; inspect before retrying.") from exc

    return call, blocking, evaluate, mutation_call


def browser_operation(request):
    operation = request["operation"]
    session = request["session"]
    target = request.get("target")
    call, blocking, evaluate, mutation_call = _calls(session, target)

    if operation == "act":
        return _act_on_tab(request, session, target, call, evaluate, mutation_call)

    info = evaluate(f"(window.__jevDrag={'true' if request.get('drag') else 'false'}, {READ_STATE})")
    if info is None:
        raise StalePage("Document is navigating")
    info["fingerprint"] = fingerprint(info)
    info["semantic_fingerprint"] = semantic_fingerprint(info)
    if request.get("screenshot", True):
        info["screenshot"] = blocking("Page.captureScreenshot", format="jpeg", quality=72)["data"]
    return info


def _act_on_tab(request, session, target, call, evaluate, mutation_call):
    """One input, or one dialog answer, keeping an input a dialog interrupted as pending (ADR-0047)."""
    action = request["action"]
    state = session_state(session, target) if session is not None and target is not None else None
    if action.get("dialog"):
        return _answer_dialog(request, state, call)
    if state is not None and state.pending is not None:
        raise MutationUncertain("An input a dialog interrupted is unfinished; observe before another input.")
    if state is not None and current_dialog(session, target) is not None:
        raise StalePage("A dialog is open on this tab. Observe again.")
    progress = {"started": False, "remaining": None, "focus": None}

    def tracked_call(method, **params):
        progress["started"] = True
        return mutation_call(method, **params)

    def tracked_evaluate(expression, *, mutation=False):
        if mutation:
            progress["started"], progress["remaining"] = True, []
        return evaluate(expression, mutation=mutation)

    try:
        return _act(request, call, tracked_evaluate, tracked_call, progress)
    except DialogOpened as exc:
        if not progress["started"]:
            # Nothing was sent yet: the page opened it on its own, so the decision is spent but safe to make again.
            raise StalePage("A dialog opened before the input. Observe again.") from exc
        # A dialog opening while the input waits is not proof that the input ran or finished: it stays pending
        # until the dialog is answered, the blocked call returns and its remaining steps are sent.
        state.pending = {"action": action["id"], "inflight": exc.inflight, "remaining": progress["remaining"],
                         "focus": progress["focus"]}
        return {"executed": action["id"], "dialog": exc.dialog, "pending": True}


def _answer_dialog(request, state, call):
    action = request["action"]
    params = {"accept": action["dialog"] == "accept"}
    if action["kind"] == "fill":
        params["promptText"] = request["text"] or ""
    try:
        call("Page.handleJavaScriptDialog", **params)
    except Exception as exc:
        raise MutationUncertain("Dialog answer result is unknown; inspect before retrying.") from exc
    if state is None:
        return {"executed": action["id"]}
    # The closed event may come later; until then the daemon still reports this dialog, which must not return.
    state.answered, state.dialog = state.dialog, None
    session = next(key for key, value in SESSIONS.items() if value is state)
    resumed = resume_pending(session, state.target)
    if "opened" in resumed:
        return {"executed": action["id"], "dialog": resumed["opened"][1], "pending": True}
    return {"executed": action["id"], **resumed}


def resume_pending(session, target):
    """Finish the input a dialog interrupted once no dialog blocks it: {} when none, {"resolved": id} when done,
    {"opened": (gen, dialog)} while a dialog still blocks it."""
    state = session_state(session, target)
    pending = state.pending
    if pending is None:
        return {}
    box, done = pending["inflight"]
    deadline = time.monotonic() + CALL_TIMEOUT_S
    while not done.wait(DIALOG_POLL_S):
        if (opened := current_dialog(session, target)) is not None:
            return {"opened": opened}
        if time.monotonic() >= deadline:
            state.pending = None
            raise MutationUncertain("An input a dialog interrupted did not finish after the dialog; inspect before retrying.")
    state.pending = None
    if "error" in box:
        raise MutationUncertain("An input a dialog interrupted failed; inspect before retrying.") from box["error"]
    remaining = pending["remaining"]
    if remaining is None:
        raise MutationUncertain("A drag a dialog interrupted is not resumed; inspect before retrying.")
    if not remaining:
        return {"resolved": pending["action"]}
    _call, _blocking, evaluate, mutation_call = _calls(session, target)
    try:
        if pending["focus"] is not None and not evaluate(
            "(n => { const e=window.__jevFast?.nodes.get(n), a=document.activeElement; "
            f"return !!e && !!a && (e===a || e.contains(a)); }})({json.dumps(pending['focus'])})"
        ):
            # The keys would go to whatever has focus now, not the field this fill chose.
            raise MutationUncertain("A fill a dialog interrupted lost its field; inspect before retrying.")
        progress = {"remaining": remaining}
        _run_steps(remaining, mutation_call, progress)
    except DialogOpened as exc:
        state.pending = {**pending, "inflight": exc.inflight, "remaining": progress["remaining"]}
        return {"opened": (exc.gen, exc.dialog)}
    return {"resolved": pending["action"]}


def _run_steps(steps, mutation_call, progress):
    """Send input steps in order, leaving in ``progress`` those not yet sent should a dialog stop one."""
    for index, (method, params) in enumerate(steps):
        progress["remaining"] = steps[index + 1:]
        mutation_call(method, **params)
    progress["remaining"] = []


def _act(request, call, evaluate, mutation_call, progress=None):
    progress = {"started": False, "remaining": None, "focus": None} if progress is None else progress
    action = request["action"]
    kind = action["kind"]
    if kind == "scroll":
        x, y = 550, 650
        if action.get("node") is not None:
            # A container-scoped scroll action (nested scrollable region): dispatch the wheel
            # event at that container's current center so scroll-chaining lands on it, not the
            # page. Re-read the rect now rather than trusting the snapshot-time position.
            point = evaluate(
                """(action => {
                  const e=window.__jevFast?.nodes.get(action.node);
                  if (!e?.isConnected) return null;
                  const r=e.getBoundingClientRect();
                  return {x:r.x+r.width/2, y:r.y+r.height/2};
                })(""" + json.dumps(action) + ")"
            )
            if point is None:
                raise StalePage("Scroll container is gone. Observe again.")
            x, y = point["x"], point["y"]
        _run_steps([("Input.dispatchMouseEvent",
                     {"type": "mouseWheel", "x": x, "y": y, "deltaX": 0, "deltaY": action["delta"]})],
                   mutation_call, progress)
    elif kind == "drag":
        return {"executed": action["id"], "events": _drag(action, evaluate, call, mutation_call)}
    elif kind != "wait":
        if type(action["node"]) is not int:
            raise ValueError("Invalid observed node")
        # Code-owned node IDs refer to actual observed elements, never model-generated selectors.
        target = evaluate("""(action => {
          let e=window.__jevFast?.nodes.get(action.node);
          if (!e?.isConnected || e.matches(':disabled') || e.closest('[aria-disabled="true"],[inert]'))
            return null;
          const targetable=action.kind==='click' ? window.__jevFast?.targetable?.(e) :
            e.checkVisibility({checkOpacity:true,checkVisibilityCSS:true});
          if (!targetable) return null;
          if (action.kind==='fill' && (e.readOnly || e.getAttribute('aria-readonly')==='true')) return null;
          // A checkbox or radio shown through its label (proxy in snapshot.js) is pressed on its own box when
          // that box has a size: clicking the label text did not toggle a styled switch (E2E-02 "VPN Connection").
          const source=window.__jevFast?.sources?.get(action.node), shown=e.getBoundingClientRect();
          let extent=shown.width*shown.height;
          if (action.kind==='click' && source && source!==e && ['checkbox','radio'].includes(source.type)) {
            const b=source.getBoundingClientRect();
            if (b.width && b.height) {
              // The control's size is its box and label together, so the root wrapping both still counts.
              extent=(Math.max(b.right,shown.right)-Math.min(b.left,shown.left))*
                (Math.max(b.bottom,shown.bottom)-Math.min(b.top,shown.top));
              e=source;
            }
          }
          let r=e.getBoundingClientRect(), x=r.x+r.width/2, y=r.y+r.height/2;
          if (!r.width || !r.height) return null;
          extent=Math.max(extent,r.width*r.height);
          // A component root that wraps the target (a styled toggle whose label ignores the pointer, E2E-02)
          // receives the click for it; a wide ancestor such as the page would not be the same control.
          const hits=(hit)=>e.contains(hit) || (hit && hit!==document.body && hit.contains(e) && (()=>{
            const w=hit.getBoundingClientRect(); return w.width*w.height<=6*extent; })());
          if (x<0 || y<0 || x>=innerWidth || y>=innerHeight || !hits(document.elementFromPoint(x,y))) {
            e.scrollIntoView({block:'center',inline:'center',behavior:'instant'});
            r=e.getBoundingClientRect(); x=r.x+r.width/2; y=r.y+r.height/2;
          }
          if (x<0 || y<0 || x>=innerWidth || y>=innerHeight ||
              !hits(document.elementFromPoint(x,y))) return null;
          if (action.kind==='select') {
            if (e.tagName!=='SELECT' || ![...e.options].some(o=>o.value===action.value &&
                !o.disabled && !o.closest('optgroup[disabled]'))) return null;
            e.value=action.value;
            e.dispatchEvent(new Event('input',{bubbles:true}));
            e.dispatchEvent(new Event('change',{bubbles:true}));
          }
          return {x,y};
        })(""" + json.dumps(action) + ")", mutation=kind == "select")
        if target is None:
            if kind == "select":
                raise MutationUncertain("Dropdown mutation was not confirmed; inspect before retrying.")
            raise StalePage("Target changed or is covered. Observe again.")
        if kind != "select":
            x, y = target["x"], target["y"]
            steps = [("Input.dispatchMouseEvent", {"type": event, "x": x, "y": y, "button": "left", "clickCount": 1})
                     for event in ("mousePressed", "mouseReleased")]
            if kind == "fill":
                modifiers = 4 if sys.platform == "darwin" else 2
                steps += [
                    ("Input.dispatchKeyEvent",
                     {"type": "keyDown", "key": "a", "code": "KeyA", "modifiers": modifiers, "commands": ["selectAll"]}),
                    ("Input.dispatchKeyEvent", {"type": "keyUp", "key": "a", "code": "KeyA", "modifiers": modifiers}),
                    ("Input.insertText", {"text": request["text"]}),
                ]
                progress["focus"] = action["node"]
            _run_steps(steps, mutation_call, progress)
    return {"executed": action["id"]}


DRAG_STEPS = 10
DRAG_STEP_S = 0.016
INTERCEPT_WAIT_S = 0.25
# Sortable-style libraries decide the drop target on a timer or on repeated over events, not on the first one.
DWELL_S = 0.05
DWELL_MOVES = 3


def _drag(action, evaluate, call, mutation_call):
    """One left-button drag between two observed nodes; returns CDP events it read that are not its own.

    A page with real mouse handlers sees a press, moves and a release. An HTML5 draggable is taken over by the
    browser as soon as the press moves, and CDP mouse events then never reach the drop target, so the drop is
    replayed as drag events from the data the browser hands back (Input.setInterceptDrags, as Playwright does).
    """

    if type(action.get("node")) is not int or type(action.get("from_node")) is not int:
        raise ValueError("Invalid observed node")
    points = evaluate("""(action => {
      const spot=(id)=>{
        const e=window.__jevFast?.nodes.get(id);
        if (!e?.isConnected || e.matches(':disabled') || !e.checkVisibility({checkOpacity:true,checkVisibilityCSS:true}))
          return null;
        const r=e.getBoundingClientRect(), x=r.x+r.width/2, y=r.y+r.height/2;
        if (!r.width || !r.height || x<0 || y<0 || x>=innerWidth || y>=innerHeight) return null;
        return e.contains(document.elementFromPoint(x,y)) ? {x,y} : null;
      };
      const from=spot(action.from_node), to=spot(action.node);
      return from && to ? {from,to} : null;
    })(""" + json.dumps(action) + ")")
    if points is None:
        raise StalePage("Drag start or end changed or is covered. Observe again.")
    start, end = points["from"], points["to"]
    path = [
        {"x": start["x"] + (end["x"] - start["x"]) * i / DRAG_STEPS,
         "y": start["y"] + (end["y"] - start["y"]) * i / DRAG_STEPS}
        for i in range(1, DRAG_STEPS + 1)
    ]
    try:
        call("Input.setInterceptDrags", enabled=True)
        intercepting = True
    except Exception:  # noqa: BLE001 - a browser without it still takes the mouse-event drag
        intercepting = False
    leftover, data = [], None

    def scan():
        nonlocal data
        for event in drain():
            if event.get("method") == "Input.dragIntercepted" and data is None:
                data = (event.get("params") or {}).get("data")
            else:
                leftover.append(event)

    mutation_call("Input.dispatchMouseEvent", type="mouseMoved", x=start["x"], y=start["y"], button="none", buttons=0)
    mutation_call("Input.dispatchMouseEvent", type="mousePressed", x=start["x"], y=start["y"],
                  button="left", buttons=1, clickCount=1)
    try:
        replay = []
        for point in path:
            if data is None:
                mutation_call("Input.dispatchMouseEvent", type="mouseMoved", x=point["x"], y=point["y"],
                              button="left", buttons=1)
                if intercepting:
                    scan()
                if data is None:
                    time.sleep(DRAG_STEP_S)
                    continue
            replay.append(point)
        if intercepting and data is None:
            # The browser reports the takeover a moment after the move that started it.
            deadline = time.monotonic() + INTERCEPT_WAIT_S
            while data is None and time.monotonic() < deadline:
                time.sleep(0.02)
                scan()
        if data is not None:
            # Libraries such as Sortable decide where to insert from where the pointer came from, so the browser's
            # remaining path is replayed as over events like a real drag, not just the last position.
            time.sleep(DWELL_S)
            over = "dragEnter"
            for point in [*replay, *[end] * DWELL_MOVES]:
                mutation_call("Input.dispatchDragEvent", type=over, x=point["x"], y=point["y"], data=data)
                mutation_call("Input.dispatchDragEvent", type="dragOver", x=point["x"], y=point["y"], data=data)
                over = "dragOver"
                time.sleep(DWELL_S)
            mutation_call("Input.dispatchDragEvent", type="drop", x=end["x"], y=end["y"], data=data)
        else:
            for _ in range(DWELL_MOVES):
                mutation_call("Input.dispatchMouseEvent", type="mouseMoved", x=end["x"], y=end["y"],
                              button="left", buttons=1)
                time.sleep(DWELL_S)
        mutation_call("Input.dispatchMouseEvent", type="mouseReleased", x=end["x"], y=end["y"],
                      button="left", buttons=0, clickCount=1)
    finally:
        if intercepting:
            try:
                call("Input.setInterceptDrags", enabled=False)
            except Exception:  # noqa: BLE001 - the session may be gone; nothing more to restore
                pass
    scan()
    return leftover
