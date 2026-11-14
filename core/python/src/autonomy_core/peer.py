"""Node mesh: signed requests to act on a peer (ADR-018).

A ``peer-request`` asks a fleet node to restart a named service or reboot its host. It travels
as the ``control.command_request`` envelope of the fleet control plane (status and silence alerts
are that plane's heartbeats). It carries the OWNER's TOTP code. ``RequestGuard`` runs on the
receiving node: signature, target, age, replay, grant, the code against the receiver's own
secret, then a cooldown. Nothing here can run an action; the caller does that through its own
governed path after ``check`` returns.

The code check is a callable (``verify_code(code, now) -> bool``) so the bot's existing TOTP
(single use per 30 s window) is reused rather than copied.
"""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from .errors import Reject
from .keys import Ed25519PrivateKey, KeyRing
from .signing import sign_object, verify_object

STATUS_MAX_AGE_S = 120
REQUEST_MAX_AGE_S = 120
COOLDOWN_S = 300

Clock = Callable[[], float]


def iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def parse_iso(text: str) -> float:
    import calendar

    try:
        return float(calendar.timegm(time.strptime(text[:19], "%Y-%m-%dT%H:%M:%S")))
    except (ValueError, TypeError) as exc:
        raise Reject("malformed", "issued_at") from exc


# ----------------------------------------------------------------------------- requests


def make_request(
    sender: str,
    target: str,
    action: str,
    key: Ed25519PrivateKey,
    totp: str,
    service: str | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    obj: dict[str, Any] = {
        "request_id": "prq_" + secrets.token_urlsafe(12),
        "from": sender,
        "to": target,
        "action": action,
        "issued_at": iso(time.time() if now is None else now),
        "nonce": secrets.token_urlsafe(16),
        "totp": totp,
    }
    if service is not None:
        obj["service"] = service
    return sign_object(obj, "peer-request", key)


@dataclass
class Grant:
    """What one peer may ask of this node: ``reboot_host`` and/or ``restart_service`` of named services."""

    actions: frozenset[str]
    services: frozenset[str] = frozenset()


@dataclass
class RequestGuard:
    """Receiver side. ``check`` returns ``(action, service)`` or raises ``Reject``."""

    me: str
    keys: KeyRing
    grants: Mapping[str, Grant]
    verify_code: Callable[[str, float], bool]
    clock: Clock = time.time
    cooldown_s: float = COOLDOWN_S
    _nonces: dict[str, float] = field(default_factory=dict, repr=False)
    _last_run: dict[tuple[str, str | None], float] = field(default_factory=dict, repr=False)

    def check(self, req: Mapping[str, Any]) -> tuple[str, str | None]:
        if req.get("to") != self.me:
            raise Reject("wrong target")
        sender = verify_object(req, "peer-request", self.keys)
        now = self.clock()
        if abs(now - parse_iso(str(req.get("issued_at")))) > REQUEST_MAX_AGE_S:
            raise Reject("stale")
        for n, t in list(self._nonces.items()):
            if now - t > 2 * REQUEST_MAX_AGE_S:
                del self._nonces[n]
        nonce = str(req.get("nonce"))
        if nonce in self._nonces:
            raise Reject("replayed")
        action = str(req.get("action"))
        service = req.get("service")
        grant = self.grants.get(sender)
        if grant is None or action not in grant.actions:
            raise Reject("not granted", f"{sender} may not {action}")
        if action == "restart_service" and (not isinstance(service, str) or service not in grant.services):
            raise Reject("not granted", f"{sender} may not restart {service}")
        # The owner's code is checked after the cheap refusals and before anything runs. A node cannot
        # produce one, so a compromised or confused peer stops here.
        code = req.get("totp")
        if not isinstance(code, str) or not self.verify_code(code, now):
            raise Reject("bad code")
        self._nonces[nonce] = now  # only a request that passed everything burns its nonce
        key = (action, service if isinstance(service, str) else None)
        last = self._last_run.get(key)
        if last is not None and now - last < self.cooldown_s:
            raise Reject("cooldown", f"{action} ran {int(now - last)} s ago")
        self._last_run[key] = now
        return action, key[1]
