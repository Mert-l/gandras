"""SQLite persistence for investigation state (one row per investigation, full state as JSON)."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone

from app.config import get_settings

_lock = threading.Lock()


def _conn() -> sqlite3.Connection:
    s = get_settings()
    p = s.resolve(s.database_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(p, timeout=10)
    c.row_factory = sqlite3.Row
    return c


def init_db() -> None:
    with _lock, _conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS investigations (
            id TEXT PRIMARY KEY, status TEXT NOT NULL, mode TEXT NOT NULL, claim TEXT NOT NULL,
            case_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, state_json TEXT NOT NULL)""")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def save(state: dict) -> None:
    state["updated_at"] = now()
    with _lock, _conn() as c:
        c.execute("""INSERT INTO investigations (id, status, mode, claim, case_id, created_at, updated_at, state_json)
                     VALUES (?,?,?,?,?,?,?,?)
                     ON CONFLICT(id) DO UPDATE SET status=excluded.status, updated_at=excluded.updated_at,
                     state_json=excluded.state_json""",
                  (state["investigation_id"], state["status"], state["mode"], state["claim"], state.get("case_id"),
                   state["created_at"], state["updated_at"], json.dumps(state, default=str)))


def load(inv_id: str) -> dict | None:
    with _lock, _conn() as c:
        r = c.execute("SELECT state_json FROM investigations WHERE id=?", (inv_id,)).fetchone()
    return json.loads(r["state_json"]) if r else None


def list_recent(limit: int = 30) -> list[dict]:
    with _lock, _conn() as c:
        rows = c.execute("SELECT state_json FROM investigations ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    out = []
    for r in rows:
        s = json.loads(r["state_json"])
        out.append({"investigation_id": s["investigation_id"], "status": s["status"], "mode": s["mode"],
                    "claim": s["claim"], "case_id": s.get("case_id"), "created_at": s["created_at"],
                    "verdict_status": (s.get("verdict") or {}).get("status")})
    return out


def mark_interrupted() -> int:
    """On startup, jobs left queued/running did not finish: mark them interrupted (never completed)."""
    n = 0
    with _lock, _conn() as c:
        for r in c.execute("SELECT state_json FROM investigations WHERE status IN ('queued','running')").fetchall():
            s = json.loads(r["state_json"])
            s["status"] = "interrupted"
            s["error"] = {"code": "INTERRUPTED", "message": "The server restarted before this investigation finished. Re-run it."}
            for st in s.get("steps", []):
                if st.get("status") == "running":
                    st["status"] = "failed"
            s["updated_at"] = now()
            c.execute("UPDATE investigations SET status=?, updated_at=?, state_json=? WHERE id=?",
                      ("interrupted", s["updated_at"], json.dumps(s, default=str), s["investigation_id"]))
            n += 1
    return n
