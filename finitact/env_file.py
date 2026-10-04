from __future__ import annotations

import os
from pathlib import Path

# The checkout's .env, not the cwd's: MCP clients start the server from an arbitrary cwd and do not pass shell variables.
REPO_ENV = Path(__file__).resolve().parents[1] / ".env"


def load_env_file(path: Path = REPO_ENV) -> None:
    """Fill unset variables from a KEY=VALUE file; variables already set by the client win."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            if value.strip():
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
