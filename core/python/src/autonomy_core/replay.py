"""State that must survive restarts: seen message ids and the sequence counter.

Extracted from edge_server's control plane (telegram-bot/controlplane.py).
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

from ._fs import atomic_write
from .errors import Reject

log = logging.getLogger("autonomy_core")

MAX_SEEN = 10000


class SeenStore:
    """Message ids already accepted, each kept until it would have expired anyway, on disk,
    so a restart does not reopen replays. ``path=None`` keeps them in memory only."""

    def __init__(self, path: Path | str | None, max_entries: int = MAX_SEEN):
        self.path = Path(path) if path is not None else None
        self.max_entries = max_entries
        self.lock = threading.Lock()
        self.ids: dict[str, float] = {}
        if self.path is None:
            return
        try:
            data = json.loads(self.path.read_text())
            self.ids = {str(k): float(v) for k, v in data.items()}
        except (OSError, ValueError, AttributeError):
            if self.path.exists():
                log.warning("%s is unreadable; starting with no seen ids", self.path)

    def add(self, message_id: str, until: float, now: float) -> bool:
        """True if new (and now remembered), False if seen before. Raises
        Reject("too many messages in flight") when full of unexpired ids."""
        with self.lock:
            self.ids = {i: e for i, e in self.ids.items() if e > now}
            if message_id in self.ids:
                return False
            if len(self.ids) >= self.max_entries:
                raise Reject("too many messages in flight")
            self.ids[message_id] = until
            self._save()
            return True

    def __contains__(self, message_id: object) -> bool:
        with self.lock:
            return message_id in self.ids

    def _save(self) -> None:
        if self.path is None:
            return
        try:
            atomic_write(self.path, json.dumps(self.ids))
        except OSError as exc:
            log.warning("cannot save seen ids to %s: %s", self.path, exc)


class Counter:
    """A number that only goes up and survives restarts (envelope ``seq``).

    Without saved state it starts from ``start`` (pass the current epoch time), which
    keeps it ahead of anything sent before a lost state file.
    """

    def __init__(self, path: Path | str | None, start: float = 0):
        self.path = Path(path) if path is not None else None
        self.lock = threading.Lock()
        self.value = int(start)
        if self.path is not None:
            try:
                self.value = max(int(self.path.read_text().strip()), 0)
            except (OSError, ValueError):
                self.value = int(start)

    def next(self) -> int:
        with self.lock:
            self.value += 1
            if self.path is not None:
                try:
                    atomic_write(self.path, str(self.value))
                except OSError as exc:
                    log.warning("cannot save the sequence number to %s: %s", self.path, exc)
            return self.value
