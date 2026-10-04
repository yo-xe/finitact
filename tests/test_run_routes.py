from concurrent.futures import ThreadPoolExecutor

import pytest

from finitact.run_routes import RunRouteLedger, request_fingerprint


def test_route_is_fixed_across_processes_and_rejects_changed_input(tmp_path):
    path = tmp_path / "routes.sqlite3"
    first = RunRouteLedger(path)
    fingerprint = request_fingerprint({"target_id": "window:7:3", "goals": [{"goal": "Fill"}]})
    route, inserted = first.reserve("run-1", fingerprint, "browser_from_window", target_id="window:7:3", tab_id="T1", origin="https://example.com")
    assert inserted and route["tab_id"] == "T1"

    second = RunRouteLedger(path)
    replay, inserted = second.reserve("run-1", fingerprint, "window", target_id="window:8:3")
    assert not inserted and replay == route
    with pytest.raises(ValueError, match="different request"):
        second.reserve("run-1", request_fingerprint({"target_id": "window:8:3"}), "window")


def test_concurrent_cross_tool_admission_has_one_winner(tmp_path):
    ledger = RunRouteLedger(tmp_path / "routes.sqlite3")
    fingerprint = request_fingerprint({"goals": [{"goal": "Save"}]})

    def reserve(route):
        return ledger.reserve("run-2", fingerprint, route)

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(reserve, ("window", "browser_from_window")))
    assert sum(inserted for _, inserted in results) == 1
    assert results[0][0] == results[1][0]
