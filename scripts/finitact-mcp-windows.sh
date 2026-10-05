#!/usr/bin/env bash
# STDIO MCP server for WSL clients (Claude Code, codex): screen input is refused from WSL Python, so this starts the
# Windows-native venv. Keys come from the repo .env because MCP clients do not pass the shell environment reliably.
set -euo pipefail
repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -f "$repo/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  . "$repo/.env"
  set +a
fi
export WSLENV="${WSLENV:+$WSLENV:}TYPESAFE_API_KEY:TYPESAFE_MODEL:TEXT_MODEL_API_KEY:TEXT_MODEL_BASE_URL:TEXT_MODEL:FINITACT_MIN_IDLE_SECONDS:FINITACT_SCREEN_UIA:FINITACT_BROWSER_LOOP:FINITACT_WINDOW_BROWSER_ROUTE:BU_CDP_URL:FINITACT_BROWSER_PROFILE:FINITACT_BROWSER_PATH"
: "${FINITACT_WINDOWS_PYTHON:?set FINITACT_WINDOWS_PYTHON (in .env) to the Windows venv python.exe}"
# The Windows interop process receives SIGPIPE when Codex closes its MCP stderr pipe during run_windows (BUG-0067).
log_dir="${XDG_STATE_HOME:-$HOME/.local/state}/finitact"
mkdir -p "$log_dir"
exec "$FINITACT_WINDOWS_PYTHON" -m finitact.mcp_server 2>> "$log_dir/mcp-server-stderr.log"
