"""
SQLite run-state persistence.

One `.db` file per run (named by run ID). Every unit of work — one
(prompt x LLM) cell in Module B, one gap-analysis item in Module D, etc. —
is written to the `units` table immediately on completion, with a status of
pending / done / failed_quota / failed_other. This makes resume-after-key-
exhaustion possible: reload state, skip everything already `done`, and
continue from the first pending/failed_quota row.

Every public method commits before returning, so a crash between calls never
loses a completed unit.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import contextmanager
from typing import Any, Iterable, Optional

PENDING = "pending"
DONE = "done"
FAILED_QUOTA = "failed_quota"
FAILED_OTHER = "failed_other"

RUN_ACTIVE = "active"
RUN_PAUSED = "paused_awaiting_key"
RUN_COMPLETE = "complete"
RUN_FAILED = "failed"

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    config_json TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS units (
    run_id TEXT NOT NULL,
    module TEXT NOT NULL,
    unit_key TEXT NOT NULL,
    status TEXT NOT NULL,
    result_json TEXT,
    error TEXT,
    updated_at REAL NOT NULL,
    PRIMARY KEY (run_id, module, unit_key)
);
"""


def db_path_for_run(run_id: str, base_dir: str = "runs") -> str:
    os.makedirs(base_dir, exist_ok=True)
    safe = "".join(c for c in run_id if c.isalnum() or c in ("-", "_"))
    return os.path.join(base_dir, f"{safe}.db")


class StateStore:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._conn = sqlite3.connect(db_path)
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self):
        self._conn.close()

    # -- runs -----------------------------------------------------------
    def create_run(self, run_id: str, config: dict):
        now = time.time()
        self._conn.execute(
            "INSERT OR IGNORE INTO runs (run_id, status, config_json, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (run_id, RUN_ACTIVE, json.dumps(config), now, now),
        )
        self._conn.commit()

    def get_run(self, run_id: str) -> Optional[dict]:
        cur = self._conn.execute(
            "SELECT run_id, status, config_json, created_at, updated_at FROM runs WHERE run_id = ?",
            (run_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        return {
            "run_id": row[0],
            "status": row[1],
            "config": json.loads(row[2]),
            "created_at": row[3],
            "updated_at": row[4],
        }

    def set_run_status(self, run_id: str, status: str):
        self._conn.execute(
            "UPDATE runs SET status = ?, updated_at = ? WHERE run_id = ?",
            (status, time.time(), run_id),
        )
        self._conn.commit()

    # -- units ------------------------------------------------------------
    def upsert_unit(
        self,
        run_id: str,
        module: str,
        unit_key: str,
        status: str,
        result: Optional[dict] = None,
        error: str = "",
    ):
        self._conn.execute(
            "INSERT INTO units (run_id, module, unit_key, status, result_json, error, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(run_id, module, unit_key) DO UPDATE SET "
            "status=excluded.status, result_json=excluded.result_json, "
            "error=excluded.error, updated_at=excluded.updated_at",
            (run_id, module, unit_key, status, json.dumps(result) if result is not None else None,
             error, time.time()),
        )
        self._conn.commit()  # atomic, immediate — never buffered only in memory

    def get_unit(self, run_id: str, module: str, unit_key: str) -> Optional[dict]:
        cur = self._conn.execute(
            "SELECT status, result_json, error FROM units WHERE run_id=? AND module=? AND unit_key=?",
            (run_id, module, unit_key),
        )
        row = cur.fetchone()
        if not row:
            return None
        return {"status": row[0], "result": json.loads(row[1]) if row[1] else None, "error": row[2]}

    def get_units(self, run_id: str, module: Optional[str] = None, status: Optional[str] = None) -> list:
        q = "SELECT module, unit_key, status, result_json, error FROM units WHERE run_id=?"
        params: list[Any] = [run_id]
        if module:
            q += " AND module=?"
            params.append(module)
        if status:
            q += " AND status=?"
            params.append(status)
        cur = self._conn.execute(q, params)
        return [
            {"module": r[0], "unit_key": r[1], "status": r[2],
             "result": json.loads(r[3]) if r[3] else None, "error": r[4]}
            for r in cur.fetchall()
        ]

    def pending_or_failed_quota(self, run_id: str, module: str) -> list:
        cur = self._conn.execute(
            "SELECT unit_key FROM units WHERE run_id=? AND module=? AND status IN (?, ?)",
            (run_id, module, PENDING, FAILED_QUOTA),
        )
        return [r[0] for r in cur.fetchall()]

    def done_keys(self, run_id: str, module: str) -> set:
        cur = self._conn.execute(
            "SELECT unit_key FROM units WHERE run_id=? AND module=? AND status=?",
            (run_id, module, DONE),
        )
        return {r[0] for r in cur.fetchall()}

    def counts(self, run_id: str, module: Optional[str] = None) -> dict:
        units = self.get_units(run_id, module=module)
        out = {DONE: 0, PENDING: 0, FAILED_QUOTA: 0, FAILED_OTHER: 0}
        for u in units:
            out[u["status"]] = out.get(u["status"], 0) + 1
        return out


@contextmanager
def open_store(run_id: str, base_dir: str = "runs"):
    store = StateStore(db_path_for_run(run_id, base_dir))
    try:
        yield store
    finally:
        store.close()


def ensure_units_pending(store: StateStore, run_id: str, module: str, unit_keys: Iterable[str]):
    """Seed a unit as `pending` if it doesn't already have a status (idempotent —
    safe to call every time a module starts, including on resume)."""
    for uk in unit_keys:
        existing = store.get_unit(run_id, module, uk)
        if existing is None:
            store.upsert_unit(run_id, module, uk, PENDING)
