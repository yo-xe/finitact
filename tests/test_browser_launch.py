import socket
import sys
import textwrap

import pytest

from finitact import browser_launch

# Stands in for Chrome: opens a port, records it in DevToolsActivePort and answers /json/version.
FAKE_BROWSER = textwrap.dedent('''\
    import http.server, pathlib, sys
    base = pathlib.Path(next(a.split("=", 1)[1] for a in sys.argv if a.startswith("--user-data-dir=")))
    pathlib.Path(sys.argv[0] + ".args").write_text("\\n".join(sys.argv[1:]))
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.end_headers(); self.wfile.write(b"{}")
        def log_message(self, *args):
            pass
    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    (base / "DevToolsActivePort").write_text(f"{server.server_port}\\n/devtools/browser/x\\n")
    server.timeout = 15
    server.handle_request(); server.handle_request()
''')


@pytest.fixture
def fake_browser(tmp_path, monkeypatch):
    script = tmp_path / "chrome"
    script.write_text(f"#!{sys.executable}\n{FAKE_BROWSER}")
    script.chmod(0o755)
    monkeypatch.setenv("FINITACT_BROWSER_PATH", str(script))
    monkeypatch.setenv("FINITACT_BROWSER_PROFILE", str(tmp_path / "profile"))
    return script


@pytest.mark.skipif(sys.platform == "win32", reason="the fake browser is a shebang script")
def test_launch_dedicated_starts_the_profile_and_returns_its_cdp_url(fake_browser, tmp_path):
    stale = tmp_path / "profile"
    stale.mkdir()
    (stale / "DevToolsActivePort").write_text("1\n/devtools/browser/old\n")
    url = browser_launch.launch_dedicated(timeout_s=10)
    assert url.startswith("http://127.0.0.1:") and not url.endswith(":1")
    args = (tmp_path / "chrome.args").read_text().splitlines()
    assert args[0] == f"--user-data-dir={tmp_path / 'profile'}" and "--remote-debugging-port=0" in args
    assert browser_launch.dedicated_endpoint() == url


def test_launch_dedicated_names_the_missing_binary(monkeypatch, tmp_path):
    monkeypatch.setattr(browser_launch, "browser_binary", lambda: None)
    with pytest.raises(RuntimeError, match="chrome-not-running: .*FINITACT_BROWSER_PATH"):
        browser_launch.launch_dedicated()


def test_a_stale_port_file_does_not_make_a_user_browser_attachable(monkeypatch, tmp_path):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        dead = probe.getsockname()[1]
    (tmp_path / "DevToolsActivePort").write_text(f"{dead}\n/devtools/browser/x\n")
    monkeypatch.setattr(browser_launch, "PROFILES", [tmp_path])
    monkeypatch.setattr(browser_launch, "USER_DEBUG_PORTS", ())
    assert browser_launch.user_browser_attachable() is False
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        (tmp_path / "DevToolsActivePort").write_text(f"{listener.getsockname()[1]}\n/devtools/browser/x\n")
        assert browser_launch.user_browser_attachable() is True
