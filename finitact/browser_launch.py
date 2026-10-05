"""Finitact's own Chrome profile, launched when no user browser accepts CDP (ADR-0054)."""

import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from browser_harness import _ipc as harness_ipc
from browser_harness.daemon import PROFILES

# A user who started Chrome with the README's --remote-debugging-port keeps their own browser.
USER_DEBUG_PORTS = (9222, 9223)
# Chrome 136+ refuses --remote-debugging-port on the default profile, so only a separate user-data-dir needs no
# chrome://inspect toggle or per-connection Allow popup.
LAUNCH_ARGS = ("--remote-debugging-port=0", "--no-first-run", "--no-default-browser-check", "about:blank")


def profile_dir() -> Path:
    if raw := os.environ.get("FINITACT_BROWSER_PROFILE"):
        return Path(raw).expanduser()
    if sys.platform == "win32" and (local := os.environ.get("LOCALAPPDATA")):
        return Path(local) / "finitact" / "browser-profile"
    return Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "finitact" / "browser-profile"


def _active_port(base: Path) -> int | None:
    try:
        first = (base / "DevToolsActivePort").read_text(encoding="utf-8", errors="replace").splitlines()[0].strip()
    except (OSError, IndexError):
        return None
    return int(first) if first.isdecimal() else None


def _answers(url: str, timeout: float = 1.0) -> bool:
    try:
        urllib.request.urlopen(f"{url}/json/version", timeout=timeout).close()
        return True
    except urllib.error.HTTPError:
        return True  # reachable; the harness reports permission states itself
    except OSError:
        return False


def _port_open(port: int) -> bool:
    # A TCP connect only: an HTTP or WebSocket request to the user's Chrome could raise its Allow popup.
    try:
        socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
        return True
    except OSError:
        return False


def dedicated_endpoint() -> str | None:
    """The CDP URL of a running Finitact profile, or None."""
    port = _active_port(profile_dir())
    url = f"http://127.0.0.1:{port}" if port else None
    return url if url and _answers(url) else None


def user_browser_attachable() -> bool:
    """A user browser already listens for CDP; a closed browser leaves a stale DevToolsActivePort behind."""
    ports = {port for base in PROFILES if (port := _active_port(base))} | set(USER_DEBUG_PORTS)
    return any(_port_open(port) for port in ports)


def _windows_candidates():
    try:
        import winreg
    except ImportError:
        winreg = None
    for exe in ("chrome.exe", "msedge.exe"):
        if winreg is not None:
            for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                try:
                    with winreg.OpenKey(hive, rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe}") as key:
                        yield winreg.QueryValue(key, None)
                except OSError:
                    pass
        app = r"Google\Chrome\Application\chrome.exe" if exe == "chrome.exe" else r"Microsoft\Edge\Application\msedge.exe"
        for root in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            if base := os.environ.get(root):
                yield str(Path(base) / app)


def browser_binary() -> str | None:
    explicit = [os.environ.get(key, "").strip() for key in ("FINITACT_BROWSER_PATH", "BH_CHROME_PATH", "CHROME_PATH")]
    if sys.platform == "win32":
        # Chrome before Edge: a fresh Edge profile signs in with the Windows account and syncs history (K-78d7db696377).
        candidates = [*explicit, *_windows_candidates()]
    elif sys.platform == "darwin":
        candidates = [*explicit, "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                      "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"]
    else:
        names = ("google-chrome-stable", "google-chrome", "chromium", "chromium-browser", "microsoft-edge")
        candidates = [*explicit, *(shutil.which(name) or "" for name in names)]
    return next((path for path in candidates if path and Path(path).expanduser().is_file()), None)


def launch_dedicated(timeout_s: float = 20.0) -> str:
    """Start Chrome on the Finitact profile and return its CDP URL."""
    binary = browser_binary()
    if binary is None:
        raise RuntimeError(
            "chrome-not-running: no browser is running and no Chrome or Edge binary was found -- "
            "set FINITACT_BROWSER_PATH, or start Chrome with --remote-debugging-port, then retry"
        )
    base = profile_dir()
    base.mkdir(parents=True, exist_ok=True)
    # A port file left by a crashed run would point at a dead port until Chrome rewrites it.
    (base / "DevToolsActivePort").unlink(missing_ok=True)
    subprocess.Popen(
        [str(Path(binary).expanduser()), f"--user-data-dir={base}", *LAUNCH_ARGS],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **harness_ipc.spawn_kwargs(),
    )
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if url := dedicated_endpoint():
            print(f"finitact: launched {Path(binary).name} on the Finitact profile {base}", file=sys.stderr)
            return url
        time.sleep(0.2)
    raise RuntimeError(
        f"chrome-not-running: {binary} started on {base} but did not open a CDP port within {timeout_s:g}s"
    )
