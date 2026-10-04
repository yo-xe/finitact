"""Durable, cross-tool admission for a run id before either coordinator can act."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any, Mapping


def request_fingerprint(request: Mapping[str, Any]) -> str:
    body = json.dumps(request, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode()).hexdigest()


class RunRouteLedger:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._database() as database:
            database.execute(
                "CREATE TABLE IF NOT EXISTS routes ("
                "run_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, route TEXT NOT NULL, "
                "target_id TEXT, drop_target_id TEXT, tab_id TEXT, origin TEXT)"
            )

    def _database(self):
        return sqlite3.connect(self.path, timeout=30)

    def get(self, run_id: str, fingerprint: str | None = None) -> dict | None:
        with self._database() as database:
            row = database.execute(
                "SELECT fingerprint, route, target_id, drop_target_id, tab_id, origin FROM routes WHERE run_id = ?", (run_id,)
            ).fetchone()
        if row is None:
            return None
        if fingerprint is not None and row[0] != fingerprint:
            raise ValueError("run_id already belongs to a different request")
        return dict(zip(("fingerprint", "route", "target_id", "drop_target_id", "tab_id", "origin"), row))

    def reserve(
        self, run_id: str, fingerprint: str, route: str, *, target_id: str | None = None,
        drop_target_id: str | None = None, tab_id: str | None = None, origin: str | None = None,
    ) -> tuple[dict, bool]:
        if route not in ("browser_direct", "browser_from_window", "window"):
            raise ValueError("unknown run route")
        with self._database() as database:
            inserted = database.execute(
                "INSERT OR IGNORE INTO routes(run_id, fingerprint, route, target_id, drop_target_id, tab_id, origin) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (run_id, fingerprint, route, target_id, drop_target_id, tab_id, origin),
            ).rowcount == 1
            row = database.execute(
                "SELECT fingerprint, route, target_id, drop_target_id, tab_id, origin FROM routes WHERE run_id = ?", (run_id,)
            ).fetchone()
        if row[0] != fingerprint:
            raise ValueError("run_id already belongs to a different request")
        return dict(zip(("fingerprint", "route", "target_id", "drop_target_id", "tab_id", "origin"), row)), inserted
