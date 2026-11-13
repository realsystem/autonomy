"""C09 event log, local part: an append-only record of what a cell observed, in SQLite (WAL).

Observations are not decisions: this is the cell's memory of the world (``observation.state``,
``cell.heartbeat``, ``station.*``), kept so that the cell can answer "what did you see, when"
after a restart or a network cut. Decisions and actions go to the audit chain (C16), not here.
Old events are pruned by age; the file survives power loss (WAL, synchronous=FULL).
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any


class EventLog:
    def __init__(self, path: Path | str):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS events ("
            " seq INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, kind TEXT NOT NULL,"
            " subject TEXT NOT NULL DEFAULT '', data TEXT NOT NULL)"
        )
        self.db.execute("CREATE INDEX IF NOT EXISTS events_kind_subject ON events(kind, subject, seq)")

    def append(self, kind: str, subject: str = "", data: dict[str, Any] | None = None, ts: float | None = None) -> int:
        with self._lock:
            cur = self.db.execute(
                "INSERT INTO events(ts, kind, subject, data) VALUES (?, ?, ?, ?)",
                (time.time() if ts is None else ts, kind, subject, json.dumps(data or {}, sort_keys=True)),
            )
            return int(cur.lastrowid or 0)

    @staticmethod
    def _row(r: tuple[Any, ...]) -> dict[str, Any]:
        return {"seq": r[0], "ts": r[1], "kind": r[2], "subject": r[3], "data": json.loads(r[4])}

    def tail(self, n: int = 20, kind: str | None = None, subject: str | None = None) -> list[dict[str, Any]]:
        where, args = [], []
        if kind:
            where.append("kind = ?")
            args.append(kind)
        if subject:
            where.append("subject = ?")
            args.append(subject)
        sql = "SELECT seq, ts, kind, subject, data FROM events"
        if where:
            sql += " WHERE " + " AND ".join(where)
        with self._lock:
            rows = self.db.execute(sql + " ORDER BY seq DESC LIMIT ?", (*args, n)).fetchall()
        return [self._row(r) for r in reversed(rows)]

    def latest(self, kind: str, subject: str = "") -> dict[str, Any] | None:
        rows = self.tail(1, kind=kind, subject=subject or None)
        return rows[0] if rows else None

    def count(self, kind: str | None = None) -> int:
        with self._lock:
            if kind:
                return int(self.db.execute("SELECT COUNT(*) FROM events WHERE kind = ?", (kind,)).fetchone()[0])
            return int(self.db.execute("SELECT COUNT(*) FROM events").fetchone()[0])

    def prune(self, older_than_s: float) -> int:
        with self._lock:
            cur = self.db.execute("DELETE FROM events WHERE ts < ?", (time.time() - older_than_s,))
            return int(cur.rowcount)

    def close(self) -> None:
        with self._lock:
            self.db.close()
