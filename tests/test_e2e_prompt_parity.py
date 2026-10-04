import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import run_e2e_live  # noqa: E402
from finitact import outer_agent  # noqa: E402

WINDOW = {"hwnd": 1, "pid": 2}


class Context(dict):
    def __missing__(self, key):
        return "x"


CONTEXT = Context(base="http://127.0.0.1:1", work=WINDOW, archive=WINDOW, window=WINDOW)


def test_every_compared_scenario_gives_both_systems_the_same_prompt():
    # public-repo plan: the comparison is fixed to identical words; anything a system needs comes from its own tools.
    for name, scenario in run_e2e_live.SCENARIOS.items():
        if scenario.get("systems", outer_agent.SYSTEMS) != outer_agent.SYSTEMS:
            continue
        finitact = scenario["prompt"](outer_agent.FINITACT, CONTEXT)
        assert finitact == scenario["prompt"](outer_agent.WINDOWS_MCP, CONTEXT), name
        assert "target_id" not in finitact and "run_windows" not in finitact and "run_browser" not in finitact, name
