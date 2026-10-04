import sys
import json
import sqlite3
import urllib.parse
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import build_report_data  # noqa: E402
import run_e2e_live  # noqa: E402


def test_restored_article_with_scroll_only_is_not_full_route(monkeypatch):
    monkeypatch.setattr(run_e2e_live, "edge_windows", lambda: [{
        "pid": 42, "url": "https://ja.wikipedia.org/wiki/有限オートマトン",
        "privacy": {"found": True, "offscreen": False},
        "lastmod": {"found": True, "offscreen": False},
    }])
    calls = [
        {"name": "mcp__finitact__run_windows", "input": {"goals": [{"goal": "Click Edge taskbar icon"}]}},
        {"name": "mcp__finitact__run_windows", "input": {"goals": [{"goal": "Press End"}]}},
    ]
    success, oracle = run_e2e_live.e2e04_judge({"tool_calls": calls})
    assert success is False
    assert oracle["final_state_success"] is True
    assert oracle["route"]["status"] == "fail"
    assert oracle["cleanup"] is None


def test_normal_taskbar_profile_is_refused_before_trial(monkeypatch):
    monkeypatch.delenv("FINITACT_E2E04_OWNED_PROFILE", raising=False)
    monkeypatch.setattr(run_e2e_live, "edge_windows", lambda: pytest.fail("Edge must not be inspected after preflight fails"))
    with pytest.raises(SystemExit, match="OWNED_PROFILE"):
        run_e2e_live.e2e04_prepare("finitact", 1)


def test_search_request_alone_does_not_prove_route():
    calls = [
        {"name": "mcp__windows_mcp__Click", "input": {"loc": [25, 1050]}},
        {"name": "mcp__windows_mcp__Type", "input": {"text": "有限オートマトン"}},
    ]
    assert run_e2e_live.e2e04_route(calls)["status"] == "unknown"
    calls = [{"name": "mcp__finitact__run_windows", "input": {
        "goals": [{"goal": "Search Wikipedia for 有限オートマトン"}]
    }}]
    assert run_e2e_live.e2e04_route(calls)["status"] == "unknown"


def test_owned_profile_history_accepts_search_then_article(monkeypatch, tmp_path):
    monkeypatch.setattr(run_e2e_live, "edge_windows", lambda: [{
        "pid": 42, "url": "https://ja.wikipedia.org/wiki/有限オートマトン",
        "privacy": {"found": True, "offscreen": False},
        "lastmod": {"found": True, "offscreen": False},
    }])
    monkeypatch.setattr(run_e2e_live, "e2e04_owned_pids", lambda profile: [42])
    closed = []
    monkeypatch.setattr(run_e2e_live, "e2e04_close_owned",
                        lambda profile: closed.append(True) or {"owned_processes": 1, "closed": True})
    # Edge flushes the article visit only on exit (BUG-0072).
    monkeypatch.setattr(run_e2e_live, "e2e04_history", lambda profile, after_id: (12, [
        {"id": 11, "url": "https://www.google.com/search?q=有限オートマトン"},
        *([{"id": 12, "url": "https://ja.wikipedia.org/wiki/" + urllib.parse.quote("有限オートマトン")}] if closed else []),
    ]))
    calls = [
        {"name": "mcp__windows_mcp__Click", "input": {"loc": [25, 1050]}},
        {"name": "mcp__windows_mcp__Type", "input": {"text": "有限オートマトン"}},
    ]
    success, oracle = run_e2e_live.e2e04_judge({
        "profile": str(tmp_path), "history_cursor": 10,
        "start_state": "owned_profile_reset_window_absent", "tool_calls": calls,
        "taskbar_rect": [0, 1032, 1920, 48],
    })
    assert success is True
    assert oracle["route"]["status"] == "pass"
    assert oracle["owned_window_found"] is True


def test_direct_article_url_alone_does_not_prove_search():
    calls = [{"name": "mcp__windows_mcp__Type", "input": {
        "text": "https://ja.wikipedia.org/wiki/有限オートマトン"
    }}]
    assert run_e2e_live.e2e04_route(calls)["status"] == "fail"


def test_missing_transient_search_history_is_unknown():
    calls = [{"name": "mcp__windows_mcp__Type", "input": {"text": "有限オートマトン"}}]
    navigation = [{"id": 8, "url": "https://ja.wikipedia.org/wiki/有限オートマトン"}]
    assert run_e2e_live.e2e04_route(calls, navigation)["status"] == "unknown"


def test_taskbar_click_after_search_does_not_satisfy_route():
    calls = [
        {"name": "mcp__windows_mcp__Type", "input": {"text": "有限オートマトン"}},
        {"name": "mcp__windows_mcp__Click", "input": {"loc": [25, 1050]}},
    ]
    navigation = [
        {"id": 11, "url": "https://www.google.com/search?q=有限オートマトン"},
        {"id": 12, "url": "https://ja.wikipedia.org/wiki/有限オートマトン"},
    ]
    assert run_e2e_live.e2e04_route(calls, navigation, [0, 1032, 1920, 48])["status"] == "unknown"


def test_history_cursor_only_reads_visits_after_start(tmp_path):
    history = tmp_path / "Default/History"
    history.parent.mkdir()
    with sqlite3.connect(history) as database:
        database.execute("CREATE TABLE urls (id INTEGER PRIMARY KEY, url TEXT)")
        database.execute("CREATE TABLE visits (id INTEGER PRIMARY KEY, url INTEGER)")
        database.executemany("INSERT INTO urls VALUES (?, ?)", [
            (1, "https://ja.wikipedia.org/wiki/有限オートマトン"),
            (2, "https://www.google.com/search?q=有限オートマトン"),
        ])
        database.executemany("INSERT INTO visits VALUES (?, ?)", [(1, 1), (2, 2), (3, 1)])
    cursor, rows = run_e2e_live.e2e04_history(tmp_path, after_id=1)
    assert cursor == 3
    assert [row["id"] for row in rows] == [2, 3]


def test_history_ignores_synced_and_imported_visits(tmp_path):
    history = tmp_path / "Default/History"
    history.parent.mkdir()
    with sqlite3.connect(history) as database:
        database.execute("CREATE TABLE urls (id INTEGER PRIMARY KEY, url TEXT)")
        database.execute("CREATE TABLE visits (id INTEGER PRIMARY KEY, url INTEGER)")
        database.execute("CREATE TABLE visit_source (id INTEGER PRIMARY KEY, source INTEGER)")
        database.executemany("INSERT INTO urls VALUES (?, ?)", [
            (1, "https://www.google.com/search?q=有限オートマトン"),
            (2, "https://ja.wikipedia.org/wiki/有限オートマトン"),
        ])
        database.executemany("INSERT INTO visits VALUES (?, ?)", [(1, 1), (2, 2), (3, 1)])
        database.executemany("INSERT INTO visit_source VALUES (?, ?)", [(1, 0), (2, 4)])
    cursor, rows = run_e2e_live.e2e04_history(tmp_path)
    assert cursor == 3
    assert [row["id"] for row in rows] == [3]


def test_reset_only_removes_contents_of_prechecked_profile(monkeypatch, tmp_path):
    monkeypatch.setattr(run_e2e_live, "WIN_HOME_WSL", tmp_path)
    profile = tmp_path / "finitact-e2e04-profiles/owned"
    profile.parent.mkdir()
    profile.mkdir()
    marker = profile / ".finitact-e2e04-owned"
    marker.write_text("finitact-e2e04-owned-v1", encoding="utf-8")
    (profile / "Default").mkdir()
    (profile / "Default/History").write_text("old session", encoding="utf-8")
    monkeypatch.setattr(run_e2e_live, "e2e04_owned_pids", lambda path: [])
    run_e2e_live.e2e04_reset_profile(profile)
    assert list(profile.iterdir()) == [marker]


def test_public_export_rejects_final_state_only_e2e04(monkeypatch, tmp_path):
    monkeypatch.setattr(build_report_data, "ROOT", tmp_path)
    monkeypatch.setattr(build_report_data, "C2_SETS", {("E2E-04", "windows-mcp"): "trial-set"})
    path = tmp_path / "docs/evaluations/e2e-live/results.jsonl"
    path.parent.mkdir(parents=True)
    row = {
        "scenario": "E2E-04", "tool": "windows-mcp", "stream": "artifacts/e2e-live/trial-set/e2e04-1.stream.jsonl",
        "oracle": {"final_state_success": True}, "success": True, "trial": 1,
        "seconds": 12.0, "outer_tokens": 1000, "model": "sonnet", "ui_tool_calls": 2,
    }
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="search route"):
        build_report_data.build_c2([])
