from scripts.summarize_phase_i import summarize


def test_invalid_environment_run_does_not_change_reported_medians():
    def record(*, valid, verdict, wall_ms, tokens):
        return {
            "outer": "codex", "case_id": "screen-case", "system": "finitact", "valid": valid,
            "external": {"driven": True, "verdict": verdict},
            "system_result": {
                "wall_ms": wall_ms, "ui_tool_calls": 1,
                "outer_tokens": {"input_tokens": tokens, "output_tokens": 0},
                "finitact_provider_tokens": {},
            },
        }

    row = summarize([
        record(valid=False, verdict="failure", wall_ms=1000, tokens=1000),
        record(valid=True, verdict="success", wall_ms=9000, tokens=9000),
    ])[0]

    assert (row["runs"], row["scored"], row["unscored"], row["successes"]) == (2, 1, 1, 1)
    assert row["median_wall_s"] == 9.0
    assert row["median_outer_tokens"] == 9000
