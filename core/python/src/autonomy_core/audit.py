"""Appending to and checking a cell's hash-chained audit log (spec audit-record).

Records are kept one per line in a JSON Lines file. The first record has seq 0 and
prev_hash GENESIS_HASH; each next record has seq + 1 and the previous record's hash.
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path
from typing import Any

from .errors import Reject
from .ids import to_rfc3339
from .keys import Ed25519PrivateKey, KeyRing
from .signing import GENESIS_HASH, audit_hash, sign_object, verify_object
from .telemetry import current_traceparent, parse_traceparent


class AuditLog:
    """Append-only, signed, hash-chained audit records for one cell."""

    def __init__(self, path: Path | str, *, cell: str, actor: str, key: Ed25519PrivateKey):
        self.path = Path(path)
        self.cell = cell
        self.actor = actor
        self.key = key
        self.lock = threading.Lock()
        self._seq, self._hash = -1, GENESIS_HASH
        for rec in self.records():
            self._seq, self._hash = int(rec["seq"]), str(rec["hash"])

    def records(self) -> Iterator[dict[str, Any]]:
        if not self.path.exists():
            return
        with open(self.path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    yield json.loads(line)

    def append(
        self,
        kind: str,
        *,
        summary: str,
        correlationid: str,
        refs: Mapping[str, str] | None = None,
        config_hash: str | None = None,
        actor: str | None = None,
        now: float | None = None,
    ) -> dict[str, Any]:
        """Append one record (fsynced before returning) and return it."""
        with self.lock:
            rec: dict[str, Any] = {
                "seq": self._seq + 1,
                "prev_hash": self._hash,
                "cell": self.cell,
                "time": to_rfc3339(time.time() if now is None else now),
                "actor": actor or self.actor,
                "kind": kind,
                "correlationid": correlationid,
                "refs": dict(refs or {}),
                "summary": summary,
            }
            if config_hash:
                rec["config_hash"] = config_hash
            ctx = parse_traceparent(current_traceparent())
            if ctx:  # ties the record to the trace of the action story (IT-71)
                rec["trace_id"] = ctx[0]
            rec["hash"] = audit_hash(rec)
            sign_object(rec, "audit-record", self.key)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                f.flush()
                os.fsync(f.fileno())
            self._seq, self._hash = rec["seq"], rec["hash"]
            return rec


def verify_chain(records: Iterable[Mapping[str, Any]], keys: KeyRing | None = None) -> list[str]:
    """Problems found in a chain (empty list: intact). Checks hashes, sequence, links,
    a single cell and, with ``keys``, every signature."""
    problems: list[str] = []
    prev: Mapping[str, Any] | None = None
    for i, rec in enumerate(records):
        where = f"record {i} (seq {rec.get('seq')})"
        try:
            if rec["hash"] != audit_hash(rec):
                problems.append(f"{where}: hash does not match")
        except KeyError as exc:
            problems.append(f"{where}: missing {exc}")
            prev = rec
            continue
        if prev is None:
            if rec.get("seq") != 0 or rec.get("prev_hash") != GENESIS_HASH:
                problems.append(f"{where}: chain does not start at seq 0 with the genesis hash")
        else:
            if rec.get("seq") != prev.get("seq", -2) + 1:
                problems.append(f"{where}: seq does not follow {prev.get('seq')}")
            if rec.get("prev_hash") != prev.get("hash"):
                problems.append(f"{where}: prev_hash does not link to the previous record")
            if rec.get("cell") != prev.get("cell"):
                problems.append(f"{where}: chain mixes cells")
        if keys is not None:
            try:
                verify_object(rec, "audit-record", keys)
            except Reject as why:
                problems.append(f"{where}: {why}")
        prev = rec
    return problems
