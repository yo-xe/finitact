from finitact.runs import Goal, WindowsRunRequest
from finitact.screen_grounded_adapter import ScreenGroundedAdapter
from finitact.windows_adapter_router import windows_adapter_factory
from finitact.windows_uia_adapter import WindowsUIAAdapter


def _request(target_id, **overrides):
    values = {
        "run_id": "live-1",
        "target_id": target_id,
        "goals": (Goal("g1", "Do the thing"),),
        "allowed_operations": ("click",),
    }
    values.update(overrides)
    return WindowsRunRequest(**values)


def test_router_dispatches_uia_prefix_to_the_uia_adapter():
    adapter = windows_adapter_factory(_request("uia:100:7"))
    assert isinstance(adapter, WindowsUIAAdapter)


def test_router_dispatches_screen_prefix_to_the_screen_grounded_adapter():
    adapter = windows_adapter_factory(_request("screen:100:7"))
    assert isinstance(adapter, ScreenGroundedAdapter)


def test_router_rejects_unknown_namespaces():
    try:
        windows_adapter_factory(_request("notepad"))
    except ValueError as exc:
        assert "uia:" in str(exc) and "screen:" in str(exc)
    else:
        raise AssertionError("an unnamespaced target_id was accepted")
