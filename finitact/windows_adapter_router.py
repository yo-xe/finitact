"""Routes a Windows run request's target_id namespace to its ActionAdapter factory.

The ``target_id`` prefix picks the observation/action strategy (UIA patterns vs screen-grounded
Stage1 candidates), never the target application -- there is no per-app ``run_unity`` tool.
"""

from __future__ import annotations

from .action_adapter import ActionAdapter
from .windows_screen_grounded import windows_screen_grounded_adapter_factory
from .windows_uia_adapter import windows_uia_adapter_factory


def windows_adapter_factory(request) -> ActionAdapter:
    prefix = request.target_id.split(":", 1)[0]
    if prefix == "uia":
        return windows_uia_adapter_factory(request)
    if prefix == "screen":
        return windows_screen_grounded_adapter_factory(request)
    raise ValueError("target_id must use uia:<HWND>:<PID> or screen:<HWND>:<PID>")
