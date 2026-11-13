"""M10: the read-only Field Station cell loop (C10 + C20 read adapter + C09 event log).

Connects to Home Assistant, records allowlisted observations (only changes, plus one snapshot
per connection), writes a heartbeat on a timer, and reconnects with backoff when Home Assistant
restarts. It cannot write to Home Assistant: it only owns an ``HaReader``.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .eventlog import EventLog
from .world_ha import Allowlist, HaError, HaReader, Observation
from .wsclient import WsError

log = logging.getLogger("autonomy_core.station")


@dataclass
class StationConfig:
    ha_url: str = "ws://supervisor/core/websocket"
    token: str = ""
    allow: list[str] = field(default_factory=list)
    db: str = "/data/station-events.db"
    heartbeat_s: float = 30.0
    retention_days: float = 30.0
    cell: str = "field-station"
    backoff_max_s: float = 30.0

    @classmethod
    def load(cls, path: Path | str) -> StationConfig:
        import json

        text = Path(path).read_text(encoding="utf-8")
        try:
            raw = json.loads(text)
        except ValueError:
            import yaml  # optional dependency: autonomy-core[cli]

            raw = yaml.safe_load(text)
        known = {k: v for k, v in (raw or {}).items() if k in cls.__dataclass_fields__}
        cfg = cls(**known)
        token_file = (raw or {}).get("token_file")
        if token_file:
            cfg.token = Path(token_file).read_text(encoding="utf-8").strip()
        cfg.token = os.environ.get("SUPERVISOR_TOKEN") or os.environ.get("HA_TOKEN") or cfg.token
        return cfg


def rss_kb() -> int:
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        pass
    return 0


class Station:
    def __init__(
        self,
        cfg: StationConfig,
        events: EventLog,
        connect: Callable[[], HaReader] | None = None,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.cfg = cfg
        self.events = events
        self.clock = clock
        self.sleep = sleep
        self.connect = connect or (lambda: HaReader(cfg.ha_url, cfg.token, Allowlist(cfg.allow)))
        self.started = clock()
        self.connected = False
        self.observed = 0
        self.heartbeats = 0
        self._last: dict[str, tuple[str, dict[str, Any]]] = {}
        self._next_heartbeat = 0.0
        self.stopping = False

    # -- one step each, so tests can drive them -------------------------------------------

    def record(self, o: Observation) -> bool:
        """Store an observation if it differs from the last one; True when stored."""
        key = (o.state, o.attributes)
        if self._last.get(o.entity_id) == key:
            return False
        self._last[o.entity_id] = key
        self.events.append(
            "observation.state",
            o.entity_id,
            {"state": o.state, "attributes": o.attributes, "last_changed": o.last_changed},
            ts=o.observed_at,
        )
        self.observed += 1
        return True

    def heartbeat(self) -> None:
        self.heartbeats += 1
        self.events.append(
            "cell.heartbeat",
            self.cfg.cell,
            {
                "n": self.heartbeats,
                "uptime_s": round(self.clock() - self.started, 1),
                "rss_kb": rss_kb(),
                "connected": self.connected,
                "entities": len(self._last),
                "observations": self.observed,
            },
        )
        self._next_heartbeat = self.clock() + self.cfg.heartbeat_s

    def session(self, reader: HaReader) -> None:
        """One connection: snapshot, then follow changes until the connection ends."""
        reader.subscribe()
        snapshot = reader.snapshot()
        for o in snapshot:
            self.record(o)
        self.connected = True
        self.events.append("station.connected", self.cfg.cell, {"entities": len(snapshot)})
        self._next_heartbeat = 0.0
        while not self.stopping:
            if self.clock() >= self._next_heartbeat:
                self.heartbeat()
                self.events.prune(self.cfg.retention_days * 86400)
            wait = max(0.1, min(1.0, self._next_heartbeat - self.clock()))
            for o in reader.events(wait):
                self.record(o)

    def run(self) -> None:
        delay = 1.0
        while not self.stopping:
            reader = None
            try:
                reader = self.connect()
                delay = 1.0
                self.session(reader)
            except (HaError, WsError, OSError) as e:
                if self.connected:
                    self.events.append("station.disconnected", self.cfg.cell, {"reason": str(e)[:200]})
                    log.warning("lost Home Assistant: %s", e)
                else:
                    log.warning("cannot reach Home Assistant: %s", e)
                self.connected = False
                self._last.clear()  # a fresh snapshot after reconnecting records everything again
                self.heartbeat()
                self.sleep(delay)
                delay = min(delay * 2, self.cfg.backoff_max_s)
            finally:
                if reader is not None:
                    reader.close()
