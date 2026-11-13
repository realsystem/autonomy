"""C20 World Adapter for Home Assistant: READ ONLY, behind an entity allowlist.

Home Assistant tokens are not entity-scoped, so the scoping is enforced here: an entity that
is not allowlisted is never returned, never stored, and cannot be asked for. This class has
no way to change anything in Home Assistant: the only messages it can send are ``auth``,
``get_states``, ``subscribe_events`` (``state_changed`` only) and ``ping``, enforced where
messages are sent, so a bug elsewhere cannot make it call a service. Writes belong to the
Action Gateway's own adapter instance (C06); nothing here holds that credential.
"""

from __future__ import annotations

import fnmatch
import json
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from .wsclient import WebSocket, WsClosed, WsError

SENDABLE = frozenset({"auth", "get_states", "subscribe_events", "ping"})
KEEP_ATTRIBUTES = ("unit_of_measurement", "device_class", "friendly_name", "state_class")


class NotAllowed(Exception):
    """The entity is not on this adapter's allowlist."""


class HaError(Exception):
    pass


class Allowlist:
    """Entity ids or shell-style patterns (``sensor.ws90_*``). An empty list allows nothing."""

    def __init__(self, patterns: Iterable[str]):
        self.patterns = [p.strip() for p in patterns if p and p.strip()]

    def allows(self, entity_id: str) -> bool:
        return any(fnmatch.fnmatchcase(entity_id, p) for p in self.patterns)

    def require(self, entity_id: str) -> None:
        if not self.allows(entity_id):
            raise NotAllowed(entity_id)


@dataclass(frozen=True)
class Observation:
    entity_id: str
    state: str
    attributes: dict[str, Any] = field(default_factory=dict)
    last_changed: str = ""
    observed_at: float = 0.0

    def age(self, now: float | None = None) -> float:
        return (time.time() if now is None else now) - self.observed_at


def _observation(raw: dict[str, Any], now: float) -> Observation:
    attrs = raw.get("attributes") or {}
    return Observation(
        entity_id=str(raw["entity_id"]),
        state=str(raw.get("state", "")),
        attributes={k: attrs[k] for k in KEEP_ATTRIBUTES if k in attrs},
        last_changed=str(raw.get("last_changed", "")),
        observed_at=now,
    )


class HaReader:
    """One WebSocket session to Home Assistant. Not thread-safe; one reader per thread."""

    def __init__(self, url: str, token: str, allowlist: Allowlist, timeout: float = 10.0):
        self.allowlist = allowlist
        self.timeout = timeout
        self.ws = WebSocket(url, timeout=timeout)
        self._id = 0
        self._cache: dict[str, Observation] = {}
        self._pending: list[dict[str, Any]] = []
        try:
            first = self._recv(timeout)
            if not first or first.get("type") != "auth_required":
                raise HaError("Home Assistant did not ask for authentication")
            self._send({"type": "auth", "access_token": token})
            reply = self._recv(timeout)
            if not reply or reply.get("type") != "auth_ok":
                raise HaError("Home Assistant refused the token")
        except BaseException:
            self.close()
            raise

    # -- the only way out of this class ----------------------------------------------------

    def _send(self, msg: dict[str, Any]) -> None:
        kind = msg.get("type")
        if kind not in SENDABLE:
            raise HaError(f"read-only adapter: {kind!r} is not a message it may send")
        if kind == "subscribe_events" and msg.get("event_type") != "state_changed":
            raise HaError("read-only adapter: it may subscribe to state_changed only")
        self.ws.send_text(json.dumps(msg))

    def _recv(self, timeout: float) -> dict[str, Any] | None:
        text = self.ws.recv_text(timeout)
        if text is None:
            return None
        try:
            msg = json.loads(text)
        except ValueError as e:
            raise HaError("Home Assistant sent something that is not JSON") from e
        return msg if isinstance(msg, dict) else None

    def _command(self, msg: dict[str, Any]) -> dict[str, Any]:
        """Send a command and wait for its result; events that arrive meanwhile are kept."""
        self._id += 1
        msg = {**msg, "id": self._id}
        self._send(msg)
        deadline = time.monotonic() + self.timeout
        while True:
            reply = self._recv(max(0.1, deadline - time.monotonic()))
            if reply is None:
                if time.monotonic() >= deadline:
                    raise HaError(f"no answer to {msg['type']}")
                continue
            if reply.get("type") == "event":
                self._pending.append(reply)
            elif reply.get("type") == "pong" and reply.get("id") == msg["id"]:
                return reply
            elif reply.get("type") == "result" and reply.get("id") == msg["id"]:
                if not reply.get("success"):
                    raise HaError(f"{msg['type']} failed: {reply.get('error')}")
                return reply

    # -- reading ---------------------------------------------------------------------------

    def subscribe(self) -> None:
        self._command({"type": "subscribe_events", "event_type": "state_changed"})

    def snapshot(self) -> list[Observation]:
        """All allowlisted entities now (and remember them)."""
        now = time.time()
        result = self._command({"type": "get_states"}).get("result") or []
        found = [_observation(s, now) for s in result if self.allowlist.allows(str(s.get("entity_id", "")))]
        for o in found:
            self._cache[o.entity_id] = o
        return found

    def events(self, timeout: float) -> list[Observation]:
        """State changes of allowlisted entities that arrived within ``timeout`` seconds."""
        raw = self._pending
        self._pending = []
        if not raw:
            msg = self._recv(timeout)
            raw = [msg] if msg else []
        out: list[Observation] = []
        now = time.time()
        for msg in raw:
            if msg.get("type") != "event":
                continue
            new = (msg.get("event") or {}).get("data", {}).get("new_state")
            if not isinstance(new, dict) or not self.allowlist.allows(str(new.get("entity_id", ""))):
                continue
            o = _observation(new, now)
            self._cache[o.entity_id] = o
            out.append(o)
        return out

    def read(self, entity_id: str) -> Observation | None:
        """The last observation of an allowlisted entity. Raises NotAllowed for any other."""
        self.allowlist.require(entity_id)
        return self._cache.get(entity_id)

    def ping(self) -> None:
        self._command({"type": "ping"})

    def close(self) -> None:
        self.ws.close()


__all__ = ["Allowlist", "HaError", "HaReader", "NotAllowed", "Observation", "WsClosed", "WsError"]
