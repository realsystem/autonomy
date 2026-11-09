"""Messages that must arrive: kept on disk and retried until delivered or expired.

Extracted from edge_server's control plane. Works for envelopes (ISO ``expires``) and for
edge_server's legacy messages (epoch ``expires``).
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ._fs import atomic_write
from .ids import parse_rfc3339

log = logging.getLogger("autonomy_core")


def message_expiry(message: dict[str, Any]) -> float:
    """Epoch expiry of a message; 0 (already expired) when it has none we can read."""
    value = message.get("expires", 0)
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return parse_rfc3339(str(value))
    except ValueError:
        return 0.0


class Outbox:
    def __init__(self, path: Path | str | None, expiry: Callable[[dict[str, Any]], float] = message_expiry):
        self.path = Path(path) if path is not None else None
        self.expiry = expiry
        self.lock = threading.Lock()
        self.items: list[dict[str, Any]] = []
        if self.path is not None:
            try:
                loaded = json.loads(self.path.read_text())
                self.items = [m for m in loaded if isinstance(m, dict)]
            except (OSError, ValueError, TypeError):
                self.items = []

    def _save(self) -> None:
        if self.path is None:
            return
        try:
            atomic_write(self.path, json.dumps(self.items))
        except OSError as exc:
            log.warning("cannot save the outbox to %s: %s", self.path, exc)

    def add(self, message: dict[str, Any]) -> None:
        with self.lock:
            self.items.append(message)
            self._save()

    def pending(self, now: float) -> list[dict[str, Any]]:
        """Unexpired messages, oldest first; expired ones are dropped."""
        with self.lock:
            kept = [m for m in self.items if self.expiry(m) > now]
            if len(kept) != len(self.items):
                self.items = kept
                self._save()
            return list(kept)

    def done(self, message_id: str) -> None:
        with self.lock:
            self.items = [m for m in self.items if m.get("id") != message_id]
            self._save()

    def __len__(self) -> int:
        with self.lock:
            return len(self.items)
