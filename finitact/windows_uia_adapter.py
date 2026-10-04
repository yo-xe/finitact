"""Concrete UI Automation ActionAdapter using a PowerShell/.NET bridge.

The bridge implements UIA patterns plus one HWND-addressed ``WM_CONTEXTMENU`` operation whose
postcondition is observed before confirmation. Shared-queue synthetic input remains unavailable
until an exclusive input environment is verified live (ADR-0006, Phase D).
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Literal

from .action_adapter import Freshness, MutationResult, Observation, ScopeViolation
from .contracts import ObservedCandidate
from .operation_vocabulary import FOCUS, OPEN_CONTEXT_MENU, is_candidate_visible, operations_for


@dataclass(frozen=True)
class WindowsTarget:
    hwnd: int
    process_id: int
    ownership: Literal["owned", "attached"] = "attached"


# CreateProcess allows 32767 chars; this leaves room for the executable and the other arguments.
ENCODED_COMMAND_LIMIT = 30_000


class PowerShellBridge:
    def __init__(self, executable: str = "powershell.exe", timeout: float = 20.0) -> None:
        self.executable = executable
        self.timeout = timeout

    def run(self, script: str, *, stdin: str | None = None) -> dict:
        """``stdin`` carries payloads that would overflow the 32767-char command line (WinError 206)."""

        encoded = base64.b64encode(script.encode("utf-16le")).decode()
        script_file = None
        if len(encoded) > ENCODED_COMMAND_LIMIT:
            # The pointer header alone nears the limit; a longer script goes as a file (E2E-01 WinError 206).
            with tempfile.NamedTemporaryFile("w", suffix=".ps1", encoding="utf-8-sig", delete=False) as handle:
                handle.write(script)
                script_file = handle.name
            command = ["-ExecutionPolicy", "Bypass", "-File", script_file]
        else:
            command = ["-EncodedCommand", encoded]
        try:
            completed = subprocess.run(
                [self.executable, "-NoProfile", "-NonInteractive", *command],
                # Without a payload the child would inherit the MCP server's stdin and could consume JSON-RPC bytes.
                **({"stdin": subprocess.DEVNULL} if stdin is None else {"input": stdin.encode("ascii")}),
                capture_output=True,
                timeout=self.timeout,
                check=False,
            )
        finally:
            if script_file is not None:
                os.unlink(script_file)
        stdout = _decode_powershell_output(completed.stdout)
        stderr = _decode_powershell_output(completed.stderr)
        if completed.returncode:
            detail = stderr.strip() or stdout.strip()
            raise RuntimeError(f"PowerShell UIA bridge failed: {detail[:500]}")
        lines = [line.strip() for line in stdout.splitlines() if line.strip()]
        if not lines:
            raise RuntimeError("PowerShell UIA bridge returned no JSON")
        try:
            return json.loads(lines[-1])
        except json.JSONDecodeError as exc:
            raise RuntimeError("PowerShell UIA bridge returned invalid JSON") from exc


def _decode_powershell_output(value: bytes) -> str:
    for encoding in ("utf-8", "cp932", "utf-16le"):
        try:
            return value.decode(encoding)
        except UnicodeDecodeError:
            continue
    return value.decode("utf-8", errors="replace")


class WindowsUIAAdapter:
    def __init__(self, target: WindowsTarget, *, bridge: PowerShellBridge | None = None) -> None:
        self.target = target
        self.ownership = target.ownership
        self.bridge = bridge or PowerShellBridge()
        self._last_candidates: dict[str, ObservedCandidate] = {}

    def observe(self) -> Observation:
        raw = self.bridge.run(_observe_script(self.target))
        if not raw.get("scope_ok"):
            raise ScopeViolation("configured HWND no longer belongs to the configured process")
        errors = tuple(str(item) for item in raw.get("errors", ()))
        complete = bool(raw.get("complete")) and not errors
        candidates: list[ObservedCandidate] = []
        untrusted_values: dict[str, str] = {}
        if complete:
            seen: set[str] = set()
            for element in raw.get("elements", ()):
                # E2E-I37: Chromium exposes each autofill suggestion twice (nested, same name and rect); four
                # identical choices split the provider's confidence to about 1/4 and every pick came back uncertain.
                twin = json.dumps(
                    [element.get(key) for key in ("scope_hwnd", "control_type", "name", "rect", "patterns")],
                    sort_keys=True,
                    default=str,
                )
                if twin in seen:
                    continue
                seen.add(twin)
                if not is_candidate_visible(
                    is_enabled=bool(element["is_enabled"]), is_offscreen=bool(element["is_offscreen"])
                ):
                    continue
                operations = operations_for(frozenset(element["patterns"]))
                if element.get("value_read_only"):
                    operations = tuple(operation for operation in operations if operation != "fill")
                if _supports_context_menu_message(element):
                    operations = (*operations, OPEN_CONTEXT_MENU)
                if _offers_focus(element):
                    operations = (*operations, FOCUS)
                for operation in operations:
                    candidate = _candidate(element, operation)
                    candidates.append(candidate)
                    if element.get("value") is not None:
                        untrusted_values[candidate.id] = str(element["value"])

        observation_id = _fingerprint(raw.get("elements", ()), semantic=False)
        semantic_id = _fingerprint(raw.get("elements", ()), semantic=True)
        observation = Observation(
            observation_id,
            semantic_id,
            tuple(candidates),
            complete,
            untrusted_values=untrusted_values,
            read_errors=errors,
        )
        self._last_candidates = {candidate.id: candidate for candidate in candidates}
        return observation

    def fresh(self, observation: Observation, candidate: ObservedCandidate | None = None) -> Freshness:
        current = self.observe()
        if current.observation_id == observation.observation_id:
            return Freshness.FRESH
        if candidate is None:
            return Freshness.STALE
        if candidate.id in self._last_candidates:
            return Freshness.FRESH
        matches = [item for item in current.candidates if _semantic_key(item) == _semantic_key(candidate)]
        return Freshness.REBOUND if len(matches) == 1 else Freshness.STALE

    def act(
        self, candidate: ObservedCandidate, observation: Observation, text: str | None = None
    ) -> MutationResult:
        if candidate.operation == "fill" and text is None:
            return MutationResult(candidate.id, "pattern", "not_attempted", "fill requires text")
        if candidate.operation == "set_range" and not _is_number(text):
            return MutationResult(candidate.id, "pattern", "not_attempted", "set_range requires a number")
        if candidate.operation == OPEN_CONTEXT_MENU:
            raw = self.bridge.run(_context_menu_script(self.target, candidate))
            if not raw.get("scope_ok"):
                raise ScopeViolation("configured HWND no longer belongs to the configured process")
            status = str(raw.get("status"))
            if status in {"stale", "not_attempted"}:
                return MutationResult(
                    candidate.id, "window_message", "not_attempted", str(raw.get("detail") or status)
                )
            if status == "uncertain":
                return MutationResult(
                    candidate.id, "window_message", "uncertain", str(raw.get("detail") or status)
                )
            if status != "confirmed":
                raise RuntimeError(str(raw.get("detail") or "WM_CONTEXTMENU failed"))
            return MutationResult(candidate.id, "window_message", "confirmed")
        if candidate.operation not in {"click", "fill", "toggle", "select", "expand_collapse", "set_range", FOCUS}:
            return MutationResult(candidate.id, "pattern", "not_attempted", "operation has no UIA pattern executor")
        raw = self.bridge.run(_act_script(self.target, candidate, text))
        if not raw.get("scope_ok"):
            raise ScopeViolation("configured HWND no longer belongs to the configured process")
        if raw.get("status") == "stale":
            return MutationResult(candidate.id, "pattern", "not_attempted", "target is stale or ambiguous")
        if raw.get("status") != "confirmed":
            raise RuntimeError(str(raw.get("detail") or "UIA pattern call failed"))
        return MutationResult(candidate.id, "pattern", "confirmed")

    def retain(self, observation: Observation) -> tuple:
        # BUG-0037: uncertain runs offer refs to pick; nothing beyond the observation is needed, since
        # fresh() re-reads the tree and act() finds the element by runtime id before any pattern call.
        return ()

    def adopt(self, observation: Observation, frames) -> None:
        self._last_candidates = {candidate.id: candidate for candidate in observation.candidates}

    def close(self) -> None:
        # Phase D initially attaches to explicitly-created disposable app processes. Process
        # lifecycle remains with the caller until owned-target shutdown is independently tested.
        return None


def windows_uia_adapter_factory(request) -> WindowsUIAAdapter:
    """Resolve the explicit ``uia:<HWND>:<PID>`` MCP target without synthetic fallback."""

    parts = request.target_id.split(":")
    if len(parts) != 3 or parts[0] != "uia" or not all(part.isdecimal() for part in parts[1:]):
        raise ValueError("target_id must use uia:<HWND>:<PID>")
    hwnd, process_id = (int(part) for part in parts[1:])
    if hwnd <= 0 or process_id <= 0:
        raise ValueError("target HWND and PID must be positive")
    return WindowsUIAAdapter(WindowsTarget(hwnd, process_id))


def _candidate(element: dict, operation: str) -> ObservedCandidate:
    runtime_id = str(element["runtime_id"])
    identity = f"{element.get('scope_hwnd')}\0{runtime_id}\0{operation}"
    candidate_id = hashlib.sha256(identity.encode()).hexdigest()[:16]
    return ObservedCandidate(
        id=candidate_id,
        operation=operation,
        label=str(element.get("name") or element.get("automation_id") or element["control_type"]),
        subject=runtime_id,
        attributes={
            "control_type": element["control_type"],
            "automation_id": element.get("automation_id", ""),
            "patterns": tuple(element["patterns"]),
            "rect": tuple(element.get("rect", ())),
            "scope_hwnd": element.get("scope_hwnd"),
            "native_window_handle": element.get("native_window_handle"),
            "selection_group": element.get("selection_group"),
            "selection_is_selected": element.get("selection_is_selected"),
        },
    )


def _supports_context_menu_message(element: dict) -> bool:
    rect = element.get("rect") or ()
    return (
        element.get("control_type") in {"ControlType.Document", "ControlType.Edit"}
        and int(element.get("native_window_handle") or 0) > 0
        and len(rect) == 4
        and float(rect[2]) > 0
        and float(rect[3]) > 0
    )


def _offers_focus(element: dict) -> bool:
    return (
        element.get("control_type") == "ControlType.Edit"
        and bool(element.get("is_keyboard_focusable"))
        and not element.get("has_keyboard_focus")
    )


def _semantic_key(candidate: ObservedCandidate) -> tuple:
    return (
        candidate.operation,
        candidate.label,
        candidate.attributes.get("control_type"),
        candidate.attributes.get("automation_id"),
        candidate.attributes.get("patterns"),
    )


def _fingerprint(elements, *, semantic: bool) -> str:
    records = []
    for element in elements:
        record = {key: value for key, value in element.items() if key != "value"}
        if semantic:
            record.pop("runtime_id", None)
            record.pop("rect", None)
        records.append(record)
    body = json.dumps(records, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode()).hexdigest()


def _observe_script(target: WindowsTarget) -> str:
    return _PS_HEADER + f"""
$hwnd = [IntPtr]{target.hwnd}
$root = [System.Windows.Automation.AutomationElement]::FromHandle($hwnd)
$scopeOk = $false
$items = @()
$errors = @()
if ($root -ne $null) {{
  $scopeOk = ($root.Current.ProcessId -eq {target.process_id})
  if ($scopeOk) {{
    $scopeHandles = @([FinitactWindowScope]::VisibleOwnedWindows({target.hwnd}, {target.process_id})) + @({target.hwnd})
    $seenRuntimeIds = New-Object 'System.Collections.Generic.HashSet[string]'
    foreach ($scopeHandle in $scopeHandles) {{
      try {{
        $scopeRoot = [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]$scopeHandle)
        if ($scopeRoot -eq $null -or $scopeRoot.Current.ProcessId -ne {target.process_id}) {{
          $errors += 'scope:mismatch'; continue
        }}
        $all = Get-FinitactDescendants $scopeRoot ($scopeHandles.Count -gt 1)
        foreach ($el in $all) {{
          try {{
            $runtimeId = ($el.GetRuntimeId() -join '.')
            if (-not $seenRuntimeIds.Add($runtimeId)) {{ continue }}
            $patterns = @($el.GetSupportedPatterns() | ForEach-Object {{ $_.ProgrammaticName }} | Sort-Object)
            $value = $null
            $valueReadOnly = $null
            $selectionGroup = $null
            $selectionIsSelected = $null
            if ($patterns -contains 'ValuePatternIdentifiers.Pattern') {{
              $vp = $el.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern)
              $value = $vp.Current.Value
              $valueReadOnly = $vp.Current.IsReadOnly
            }} elseif ($patterns -contains 'RangeValuePatternIdentifiers.Pattern') {{
              $rp = $el.GetCurrentPattern([System.Windows.Automation.RangeValuePattern]::Pattern)
              $value = $rp.Current.Value.ToString([Globalization.CultureInfo]::InvariantCulture)
            }}
            if ($patterns -contains 'SelectionItemPatternIdentifiers.Pattern') {{
              $sp = $el.GetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern)
              $selectionIsSelected = $sp.Current.IsSelected
              $container = $sp.Current.SelectionContainer
              if ($container -ne $null) {{
                $selectionGroup = ([Int64]$scopeHandle).ToString() + ':' + ($container.GetRuntimeId() -join '.')
              }}
            }}
            $r = $el.Current.BoundingRectangle
            $items += [ordered]@{{
              runtime_id=$runtimeId; scope_hwnd=[Int64]$scopeHandle;
              control_type=$el.Current.ControlType.ProgrammaticName;
              native_window_handle=$el.Current.NativeWindowHandle;
              automation_id=$el.Current.AutomationId; name=$el.Current.Name; is_enabled=$el.Current.IsEnabled;
              is_offscreen=$el.Current.IsOffscreen; patterns=$patterns; value=$value;
              is_keyboard_focusable=$el.Current.IsKeyboardFocusable; has_keyboard_focus=$el.Current.HasKeyboardFocus;
              value_read_only=$valueReadOnly;
              selection_group=$selectionGroup; selection_is_selected=$selectionIsSelected;
              rect=@($r.X,$r.Y,$r.Width,$r.Height)
            }}
          }} catch {{ $errors += ('element:' + $_.Exception.GetType().Name) }}
        }}
      }} catch {{ $errors += ('walk:' + $_.Exception.GetType().Name) }}
    }}
  }}
}}
$result = [ordered]@{{
  scope_ok=$scopeOk; complete=($scopeOk -and $errors.Count -eq 0)
  elements=@($items); errors=@($errors)
}}
$result | ConvertTo-Json -Compress -Depth 7
"""


def _act_script(target: WindowsTarget, candidate: ObservedCandidate, text: str | None) -> str:
    runtime = _ps_string(str(candidate.subject))
    scope_hwnd = int(candidate.attributes.get("scope_hwnd") or target.hwnd)
    operation = _ps_string(candidate.operation)
    encoded_text = base64.b64encode((text or "").encode()).decode()
    return _PS_HEADER + f"""
$hwnd = [IntPtr]{target.hwnd}
$root = [System.Windows.Automation.AutomationElement]::FromHandle($hwnd)
$scopeOk = ($root -ne $null -and $root.Current.ProcessId -eq {target.process_id})
$status = 'stale'; $detail = $null
if ($scopeOk) {{
  try {{
    $matches = @()
    $wantedScope = [Int64]{scope_hwnd}
    $scopeHandles = @([FinitactWindowScope]::VisibleOwnedWindows({target.hwnd}, {target.process_id})) + @({target.hwnd})
    foreach ($scopeHandle in $scopeHandles) {{
      if ([Int64]$scopeHandle -ne $wantedScope) {{ continue }}
      $scopeRoot = [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]$scopeHandle)
      if ($scopeRoot -ne $null -and $scopeRoot.Current.ProcessId -eq {target.process_id}) {{
        $all = Get-FinitactDescendants $scopeRoot ($scopeHandles.Count -gt 1)
        foreach ($el in $all) {{ if (($el.GetRuntimeId() -join '.') -eq {runtime}) {{ $matches += $el }} }}
      }}
    }}
    if ($matches.Count -eq 1) {{
      $el = $matches[0]; $op = {operation}
      if ($op -eq 'click') {{ $el.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke() }}
      elseif ($op -eq 'fill') {{
        $text = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{encoded_text}'))
        $el.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern).SetValue($text)
      }}
      elseif ($op -eq 'toggle') {{ $el.GetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern).Toggle() }}
      elseif ($op -eq 'select') {{
        $el.GetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern).Select()
      }}
      elseif ($op -eq 'expand_collapse') {{
        $p=$el.GetCurrentPattern([System.Windows.Automation.ExpandCollapsePattern]::Pattern)
        if ($p.Current.ExpandCollapseState -eq
            [System.Windows.Automation.ExpandCollapseState]::Collapsed) {{$p.Expand()}}
        else {{$p.Collapse()}}
      }}
      elseif ($op -eq 'set_range') {{
        $p=$el.GetCurrentPattern([System.Windows.Automation.RangeValuePattern]::Pattern)
        $p.SetValue([double]::Parse([System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{encoded_text}')),[Globalization.CultureInfo]::InvariantCulture))
      }}
      elseif ($op -eq 'focus') {{
        $el.SetFocus()
        # Browsers move focus asynchronously after SetFocus returns.
        for ($i = 0; $i -lt 10 -and -not $el.Current.HasKeyboardFocus; $i++) {{ Start-Sleep -Milliseconds 50 }}
        if (-not $el.Current.HasKeyboardFocus) {{ throw 'SetFocus returned but the element does not have keyboard focus' }}
      }}
      $status = 'confirmed'
    }}
  }} catch {{ $status='error'; $detail=$_.Exception.GetType().Name + ':' + $_.Exception.Message }}
}}
[ordered]@{{scope_ok=$scopeOk;status=$status;detail=$detail}} | ConvertTo-Json -Compress
"""


def _context_menu_script(target: WindowsTarget, candidate: ObservedCandidate) -> str:
    runtime = _ps_string(str(candidate.subject))
    scope_hwnd = int(candidate.attributes.get("scope_hwnd") or target.hwnd)
    expected_native = int(candidate.attributes.get("native_window_handle") or 0)
    return _PS_HEADER + f"""
$root = [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]{target.hwnd})
$scopeOk = ($root -ne $null -and $root.Current.ProcessId -eq {target.process_id})
$status = 'stale'; $detail = $null
if ($scopeOk) {{
  try {{
    $wantedScope = [Int64]{scope_hwnd}; $matches = @()
    $scopeHandles = @([FinitactWindowScope]::VisibleOwnedWindows({target.hwnd}, {target.process_id})) + @({target.hwnd})
    foreach ($scopeHandle in $scopeHandles) {{
      if ([Int64]$scopeHandle -ne $wantedScope) {{ continue }}
      $scopeRoot = [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]$scopeHandle)
      if ($scopeRoot -eq $null -or $scopeRoot.Current.ProcessId -ne {target.process_id}) {{ continue }}
      $all = Get-FinitactDescendants $scopeRoot ($scopeHandles.Count -gt 1)
      foreach ($el in $all) {{ if (($el.GetRuntimeId() -join '.') -eq {runtime}) {{ $matches += $el }} }}
    }}
    if ($matches.Count -eq 1) {{
      $el = $matches[0]; $native = [Int64]$el.Current.NativeWindowHandle
      $r = $el.Current.BoundingRectangle
      if ($native -le 0 -or $native -ne [Int64]{expected_native} -or
          $r.Width -le 0 -or $r.Height -le 0) {{
        $status='not_attempted'; $detail='native target changed'
      }} elseif (-not [FinitactWindowScope]::WindowBelongsToProcess($native, {target.process_id})) {{
        $status='not_attempted'; $detail='native target process mismatch'
      }} else {{
        $before = @([FinitactWindowScope]::VisibleOwnedWindows({target.hwnd}, {target.process_id}))
        $x = [int]($r.X + [Math]::Min([Math]::Max($r.Width / 2, 1), $r.Width - 1))
        $y = [int]($r.Y + [Math]::Min([Math]::Max($r.Height / 2, 1), $r.Height - 1))
        $sent = [FinitactWindowScope]::SendContextMenu($native, $x, $y)
        if (-not $sent) {{ $status='not_attempted'; $detail='SendNotifyMessage rejected' }}
        else {{
          $popup = 0
          for ($i=0; $i -lt 20 -and $popup -eq 0; $i++) {{
            Start-Sleep -Milliseconds 50
            $after = @([FinitactWindowScope]::VisibleOwnedWindows({target.hwnd}, {target.process_id}))
            foreach ($handle in $after) {{
              if ($before -notcontains $handle) {{
                $popupRoot = [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]$handle)
                if ($popupRoot -ne $null -and $popupRoot.Current.ProcessId -eq {target.process_id}) {{
                  $menuItem = $popupRoot.FindFirst(
                    [System.Windows.Automation.TreeScope]::Descendants,
                    (New-Object System.Windows.Automation.PropertyCondition(
                      [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
                      [System.Windows.Automation.ControlType]::MenuItem)))
                  if ($menuItem -ne $null) {{ $popup = $handle; break }}
                }}
              }}
            }}
          }}
          if ($popup -ne 0) {{ $status='confirmed' }}
          else {{ $status='uncertain'; $detail='message accepted but no new owned popup was observed' }}
        }}
      }}
    }}
  }} catch {{ $status='error'; $detail=$_.Exception.GetType().Name + ':' + $_.Exception.Message }}
}}
[ordered]@{{scope_ok=$scopeOk;status=$status;detail=$detail}} | ConvertTo-Json -Compress
"""


def _ps_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


_PS_HEADER = """[Console]::OutputEncoding=[System.Text.Encoding]::UTF8
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type @"
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
public static class FinitactWindowScope {
    private delegate bool EnumWindowsProc(IntPtr hwnd, IntPtr parameter);
    [DllImport("user32.dll")] private static extern bool EnumWindows(EnumWindowsProc callback, IntPtr parameter);
    [DllImport("user32.dll")] private static extern IntPtr GetWindow(IntPtr hwnd, uint command);
    [DllImport("user32.dll")] private static extern uint GetWindowThreadProcessId(IntPtr hwnd, out uint processId);
    [DllImport("user32.dll")] private static extern bool IsWindowVisible(IntPtr hwnd);
    [DllImport("user32.dll", SetLastError=true)]
    private static extern bool SendNotifyMessage(IntPtr hwnd, uint message, IntPtr wParam, IntPtr lParam);
    private const uint GW_OWNER = 4;
    private const uint WM_CONTEXTMENU = 0x007B;

    public static bool WindowBelongsToProcess(long hwndValue, int expectedProcessId) {
        uint processId;
        GetWindowThreadProcessId(new IntPtr(hwndValue), out processId);
        return processId == expectedProcessId;
    }

    public static bool SendContextMenu(long hwndValue, int x, int y) {
        var hwnd = new IntPtr(hwndValue);
        long coordinates = ((long)(ushort)x) | ((long)(ushort)y << 16);
        return SendNotifyMessage(hwnd, WM_CONTEXTMENU, hwnd, new IntPtr(coordinates));
    }

    public static long[] VisibleOwnedWindows(long rootValue, int expectedProcessId) {
        var root = new IntPtr(rootValue);
        var result = new List<long>();
        EnumWindows((hwnd, _) => {
            uint processId;
            GetWindowThreadProcessId(hwnd, out processId);
            if (processId != expectedProcessId || !IsWindowVisible(hwnd) || hwnd == root) return true;
            var owner = GetWindow(hwnd, GW_OWNER);
            var visited = new HashSet<IntPtr>();
            while (owner != IntPtr.Zero && visited.Add(owner)) {
                if (owner == root) { result.Add(hwnd.ToInt64()); break; }
                owner = GetWindow(owner, GW_OWNER);
            }
            return true;
        }, IntPtr.Zero);
        result.Sort();
        return result.ToArray();
    }
}
"@
# E2E-I38: with an owned popup shown, Chromium lists the window's top-level panes again below themselves, and
# FindAll(Descendants) returned each element dozens of times (about 3 s, and an act target became ambiguous).
function Get-FinitactDescendants($root, [bool]$once) {
  $all = [System.Windows.Automation.Condition]::TrueCondition
  if (-not $once) { return $root.FindAll([System.Windows.Automation.TreeScope]::Descendants, $all) }
  $seen = New-Object 'System.Collections.Generic.HashSet[string]'
  $found = New-Object 'System.Collections.Generic.List[System.Windows.Automation.AutomationElement]'
  $pending = New-Object 'System.Collections.Generic.Stack[System.Windows.Automation.AutomationElement]'
  $pending.Push($root)
  while ($pending.Count -gt 0) {
    $element = $pending.Pop()
    if (-not [object]::ReferenceEquals($element, $root)) { $found.Add($element) }
    $fresh = @()
    foreach ($child in $element.FindAll([System.Windows.Automation.TreeScope]::Children, $all)) {
      if ($seen.Add(($child.GetRuntimeId() -join '.'))) { $fresh += $child }
    }
    for ($i = $fresh.Count - 1; $i -ge 0; $i--) { $pending.Push($fresh[$i]) }
  }
  return ,$found
}
"""


def _is_number(text: str | None) -> bool:
    try:
        return text is not None and math.isfinite(float(text.strip()))
    except ValueError:
        return False
