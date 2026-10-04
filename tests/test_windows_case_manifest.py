import json
from pathlib import Path

MANIFEST = Path(__file__).parents[1] / "docs/evaluations/windows-mcp-replacement/case-manifest.json"


def test_windows_replacement_case_manifest_has_stable_bounded_contracts():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    cases = manifest["cases"]
    assert manifest["schema_version"] == 1
    assert manifest["status"] == "preregistered"
    assert manifest["live_execution_status"] in ("not_run", "partially_run", "completed")
    assert {case["group"] for case in cases} == {
        "uia_single_action",
        "screen_single_action",
        "screen_two_action",
        "fail_closed",
    }
    assert len({case["id"] for case in cases}) == len(cases)

    required = {
        "initial_state",
        "goal",
        "candidate_set",
        "allowed_operations",
        "success_predicate",
        "independent_oracle",
        "deadline_ms",
        "action_budget",
        "provider_attempt_budget",
        "stop_conditions",
        "expected_send_input_events",
    }
    for case in cases:
        assert required <= case.keys()
        assert case["allowed_operations"]
        assert case["stop_conditions"]
        assert min(case["deadline_ms"], case["action_budget"], case["provider_attempt_budget"]) > 0


def test_fail_closed_preregistration_includes_all_phase_f_pre_delivery_gates():
    failure_cases = {case["id"]: case for case in json.loads(MANIFEST.read_text())["cases"]}
    for case_id in (
        "screen-fail-mutex-contention-001",
        "screen-fail-idle-short-001",
        "screen-fail-scope-change-001",
        "screen-fail-indicator-readiness-001",
    ):
        assert failure_cases[case_id]["expected_send_input_events"] == 0

    uncertain = failure_cases["screen-fail-post-delivery-uncertain-001"]
    assert uncertain["expected_send_input_events"] == "0_or_2_but_never_retried"
    assert "subsequent run" in uncertain["success_predicate"]
