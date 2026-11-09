"""Approval & owner control (C05).

``ApprovalService`` keeps approval requests for ``require_approval`` decisions and turns
an owner's answer into a signed Approval, attested by this service: it signs only after
the caller has shown the owner is who they claim (allowed operator, private chat,
authenticator code) and the request has not expired. The approval binds the decision id
and ``params_hash``, so any parameter change makes it useless at the gateway (IT-12).

``ControlState`` holds the owner's emergency controls: ``suspend`` a component or a cell,
``hold`` everything automatic, ``resume``. Orders are signed, kept on disk (sticky across
restarts) and need nothing outside the cell (ST-08). Gateways ask ``blocked(principal)``
before every action; owner-initiated actions are never blocked.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

from ._fs import atomic_write
from .errors import Reject
from .ids import parse_rfc3339, prefixed_id, to_rfc3339
from .keys import Ed25519PrivateKey
from .signing import sign_object

APPROVAL_TTL = 900.0


class ApprovalService:
    def __init__(
        self,
        state_dir: Path | str,
        *,
        key: Ed25519PrivateKey,
        attester: str,
        operators: Iterable[int | str],
        clock: Callable[[], float] = time.time,
        ttl: float = APPROVAL_TTL,
    ):
        self.path = Path(state_dir) / "approvals.json"
        self.key = key
        self.attester = attester
        self.operators = {str(o) for o in operators}
        self.clock = clock
        self.ttl = ttl
        self.lock = threading.Lock()
        try:
            self.requests: dict[str, dict[str, Any]] = json.loads(self.path.read_text())
        except (OSError, ValueError):
            self.requests = {}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(self.path, json.dumps(self.requests, ensure_ascii=False))

    def _expire(self, now: float) -> None:
        for rid in [r for r, q in self.requests.items() if q["expires_at"] <= now]:
            del self.requests[rid]

    def request(
        self, proposal: Mapping[str, Any], decision: Mapping[str, Any], summary: str
    ) -> dict[str, Any]:
        """Ask the owner. Expires with the decision or after ``ttl``, whichever is first."""
        if decision.get("effect") != "require_approval":
            raise Reject("validation.malformed", "only require_approval decisions need an approval")
        now = self.clock()
        expires = min(parse_rfc3339(str(decision["expires"])), now + self.ttl)
        with self.lock:
            self._expire(now)
            rid = prefixed_id("apq", now)
            self.requests[rid] = {
                "request_id": rid,
                "proposal": dict(proposal),
                "decision": dict(decision),
                "summary": summary[:500],
                "created_at": now,
                "expires_at": expires,
            }
            self._save()
            return self.requests[rid]

    def pending(self) -> list[dict[str, Any]]:
        with self.lock:
            self._expire(self.clock())
            return sorted(self.requests.values(), key=lambda q: q["created_at"])

    def approve(
        self,
        request_id: str,
        *,
        operator: int | str,
        private_chat: bool,
        code_ok: bool,
        method: str = "telegram_totp",
        comment: str = "",
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """(request, signed Approval). Raises Reject; a refused request stays pending."""
        now = self.clock()
        with self.lock:
            self._expire(now)
            q = self.requests.get(request_id)
            if q is None:
                raise Reject(
                    "validation.unknown_request", f"{request_id} is not pending (expired or answered)"
                )
            if str(operator) not in self.operators:
                raise Reject("validation.operator", "not an allowed operator")
            if not private_chat:
                raise Reject("validation.chat", "approvals are only accepted in a private chat")
            if not code_ok:
                raise Reject("validation.code", "the authenticator code is not valid")
            d = q["decision"]
            approval: dict[str, Any] = {
                "approval_id": prefixed_id("apr", now),
                "decision_id": d["decision_id"],
                "params_hash": d["params_hash"],
                "approver": "owner:ops",
                "attested_by": self.attester,
                "method": method,
                "approved_at": to_rfc3339(now),
                "expires": to_rfc3339(q["expires_at"]),
            }
            if comment:
                approval["comment"] = comment[:1000]
            sign_object(approval, "approval", self.key)
            del self.requests[request_id]
            self._save()
            return q, approval

    def attest(
        self, decision: Mapping[str, Any], *, operator: int | str, private_chat: bool, code_ok: bool
    ) -> dict[str, Any]:
        """Approve a decision the owner asked for in the same message (e.g. /reboot <code>)."""
        q = self.request({"proposal_id": decision.get("proposal_id")}, decision, "owner command")
        return self.approve(q["request_id"], operator=operator, private_chat=private_chat, code_ok=code_ok)[1]

    def deny(self, request_id: str) -> dict[str, Any] | None:
        with self.lock:
            q = self.requests.pop(request_id, None)
            if q is not None:
                self._save()
            return q


class ControlState:
    def __init__(
        self,
        state_dir: Path | str,
        *,
        key: Ed25519PrivateKey,
        attester: str,
        cell: str,
        clock: Callable[[], float] = time.time,
    ):
        self.path = Path(state_dir) / "control.json"
        self.key = key
        self.attester = attester
        self.cell = cell
        self.clock = clock
        self.lock = threading.Lock()
        try:
            self.orders: dict[str, dict[str, Any]] = json.loads(self.path.read_text())  # scope -> order
        except (OSError, ValueError):
            self.orders = {}

    def order(self, kind: str, scope: str, reason: str) -> dict[str, Any]:
        """Issue and apply a control order for the owner (the caller verified them)."""
        if kind not in ("suspend", "hold", "resume"):
            raise Reject("validation.malformed", f"unknown order {kind}")
        if kind == "hold":
            scope = "all"
        now = self.clock()
        o = sign_object(
            {
                "order_id": prefixed_id("ord", now),
                "kind": kind,
                "scope": scope,
                "reason": reason[:500] or kind,
                "issued_by": "owner:ops",
                "attested_by": self.attester,
                "issued_at": to_rfc3339(now),
            },
            "control-order",
            self.key,
        )
        with self.lock:
            if kind == "resume":
                if scope == "all":
                    self.orders.clear()
                else:
                    self.orders.pop(scope, None)
            else:
                self.orders[scope] = o
            self.path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write(self.path, json.dumps(self.orders, ensure_ascii=False))
        return o

    def blocked(self, principal: str) -> str | None:
        """Why an action by ``principal`` must not run now, or None."""
        if principal.startswith("owner:"):
            return None
        base = principal.split("@", 1)[0]
        with self.lock:
            for scope in ("all", f"cell:{self.cell}", base, principal):
                o = self.orders.get(scope)
                if o is not None:
                    what = (
                        "everything automatic is on hold" if o["kind"] == "hold" else f"{scope} is suspended"
                    )
                    return f"{what} ({o['reason']})"
        return None

    def active(self) -> list[dict[str, Any]]:
        with self.lock:
            return list(self.orders.values())
