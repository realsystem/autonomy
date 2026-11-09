"""Action Gateway (C06): the only thing that executes consequential actions.

``ActionGateway.execute(action_request)`` re-verifies everything it is given, in order:

1. the cell is not suspended;
2. the request is well formed, the decision is for this proposal and binds its exact
   action (``params_hash``), is signed by a trusted PDP, unexpired, and not a deny;
3. ``require_approval`` decisions carry an owner approval for this decision and these
   parameters, signed by a current owner key, unexpired;
4. every obligation is understood (unknown ones stop execution);
5. idempotency: the same ``idempotency_key`` returns the first record, never runs twice;
6. an adapter declares the action;
7. rate limits from the decision; for automatic actions (principal not the owner) at
   most ``auto_per_target_hour`` per target per hour, and a target whose last automatic
   action failed is closed for ``breaker_hold`` seconds (circuit breaker, IT-15);
8. a write-ahead journal entry (fsynced) before the adapter is called.

On start-up, journal entries without an outcome are reconciled with the adapter; if the
adapter cannot tell, the action is recorded ``unknown`` and audited for the owner.
Every outcome is a signed ExecutionRecord and an audit record.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from .audit import AuditLog
from .errors import Reject
from .ids import parse_rfc3339, to_rfc3339
from .keys import Ed25519PrivateKey, KeyRing
from .pdp import verify_owner_signed
from .registry import Registry
from .signing import params_hash, sign_object, verify_object

log = logging.getLogger("autonomy_core.gateway")

KNOWN_OBLIGATIONS = {"verify", "rate_limit", "approval", "notify", "dry_run"}
TERMINAL = {"succeeded", "failed", "rejected", "unknown", "cancelled"}


@dataclass
class AdapterResult:
    """What an adapter reports. ``started`` means accepted and still running (asynchronous
    adapters report the outcome later through ``ActionGateway.complete``)."""

    status: str  # succeeded | failed | started
    response: dict[str, Any] = field(default_factory=dict)
    before: Any = None
    after: Any = None
    error: str = ""


class Adapter(Protocol):
    """C20: the only code with write credentials for a system."""

    name: str

    def actions(self) -> set[str]: ...

    def execute(self, action_id: str, action: Mapping[str, Any]) -> AdapterResult: ...

    def reconcile(self, action_id: str, action: Mapping[str, Any]) -> AdapterResult | None:
        """After a crash: the outcome of an action that was started, or None if unknown."""
        ...


class MockAdapter:
    """For tests and dry runs: records calls, optionally fails or crashes."""

    def __init__(self, name: str = "adapter:mock", actions: set[str] | None = None):
        self.name = name
        self._actions = actions or {"mock.set"}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.state: dict[str, Any] = {}
        self.fail_next = False
        self.crash_next = False
        self.known: dict[str, AdapterResult] = {}

    def actions(self) -> set[str]:
        return set(self._actions)

    def execute(self, action_id: str, action: Mapping[str, Any]) -> AdapterResult:
        crash, self.crash_next = self.crash_next, False
        self.calls.append((action_id, dict(action)))
        if self.fail_next:
            self.fail_next = False
            result = AdapterResult("failed", {"ok": False}, error="simulated failure")
        else:
            before = self.state.get(action["target"])
            self.state[action["target"]] = dict(action.get("params", {})).get("value")
            result = AdapterResult("succeeded", {"ok": True}, before, self.state[action["target"]])
        self.known[action_id] = result
        if crash:
            raise SystemExit("simulated crash after the write")
        return result

    def reconcile(self, action_id: str, action: Mapping[str, Any]) -> AdapterResult | None:
        return self.known.get(action_id)


class Journal:
    """Append-only JSON Lines journal of action events, fsynced."""

    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()

    def append(self, entry: dict[str, Any]) -> None:
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                f.flush()
                os.fsync(f.fileno())

    def entries(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        out = []
        with open(self.path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except ValueError:
                    log.warning("journal: skipping a damaged line in %s", self.path)
        return out


class ActionGateway:
    def __init__(
        self,
        *,
        executor: str,
        key: Ed25519PrivateKey,
        state_dir: Path | str,
        pdp_keys: KeyRing,
        registry: Registry | None,
        adapters: list[Adapter],
        audit: AuditLog | None = None,
        clock: Callable[[], float] = time.time,
        suspended: Callable[[], bool] = lambda: False,
        auto_per_target_hour: int = 2,
        breaker_hold: float = 24 * 3600.0,
    ):
        self.executor = executor
        self.key = key
        self.dir = Path(state_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.journal = Journal(self.dir / "actions.jsonl")
        self.pdp_keys = pdp_keys
        self.registry = registry
        self.adapters: dict[str, Adapter] = {}
        for adapter in adapters:
            for a in adapter.actions():
                self.adapters[a] = adapter
        self.audit = audit
        self.clock = clock
        self.suspended = suspended
        self.auto_per_target_hour = auto_per_target_hour
        self.breaker_hold = breaker_hold
        self.lock = threading.RLock()
        self.records: dict[str, dict[str, Any]] = {}  # action_id -> latest record
        self.by_key: dict[str, str] = {}  # idempotency_key -> action_id
        self.started: dict[str, dict[str, Any]] = {}  # action_id -> journal "started" entry
        self._load()

    # -- state

    def _load(self) -> None:
        for e in self.journal.entries():
            aid = e.get("action_id")
            if not isinstance(aid, str):
                continue
            if e.get("event") == "started":
                self.started[aid] = e
                self.by_key.setdefault(str(e.get("idempotency_key")), aid)
            rec = e.get("record")
            if isinstance(rec, dict):
                self.records[aid] = rec
                if rec.get("status") in TERMINAL:
                    self.started.pop(aid, None)
                if e.get("idempotency_key"):
                    self.by_key.setdefault(str(e["idempotency_key"]), aid)

    def reconcile(self) -> list[dict[str, Any]]:
        """Settle actions that were running when the process stopped. Call at start-up."""
        settled = []
        with self.lock:
            for aid, entry in list(self.started.items()):
                if self.records.get(aid, {}).get("status") == "started" and entry.get("async"):
                    continue  # still legitimately running in an asynchronous adapter
                adapter = self.adapters.get(str(entry["action"]["type"]))
                result = adapter.reconcile(aid, entry["action"]) if adapter else None
                if result is None or result.status == "started":
                    rec = self._finish(
                        entry, AdapterResult("unknown", error="interrupted; outcome unknown"), reconciled=True
                    )
                else:
                    rec = self._finish(entry, result, reconciled=True)
                settled.append(rec)
        return settled

    def history(self, since: float) -> list[dict[str, Any]]:
        out = []
        for e in self.journal.entries():
            if e.get("event") == "started" and float(e.get("at", 0)) >= since:
                out.append(e)
        return out

    def record(self, action_id: str) -> dict[str, Any] | None:
        return self.records.get(action_id)

    # -- execute

    def execute(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """Run an ActionRequest; always returns a signed ExecutionRecord."""
        with self.lock:
            aid = str(request.get("action_id", "")) if isinstance(request, Mapping) else ""
            key = str(request.get("idempotency_key", "")) if isinstance(request, Mapping) else ""
            if key and key in self.by_key:
                previous = self.records.get(self.by_key[key])
                if previous is not None:
                    return previous
            try:
                action, automatic, obligations = self._check(request)
            except Reject as why:
                return self._reject(aid or "act_invalid", request, why)
            adapter = self.adapters[action["type"]]
            now = self.clock()
            entry = {
                "event": "started",
                "action_id": aid,
                "at": now,
                "idempotency_key": key,
                "action": action,
                "automatic": automatic,
                "verify": "verify" in obligations,
                "dry_run": "dry_run" in obligations,
                "adapter": adapter.name,
                "refs": _refs(request),
                "correlationid": str(request.get("correlationid") or "corr_" + aid[4:]),
            }
            self.by_key[key] = aid
            if "dry_run" in obligations:
                return self._finish(entry, AdapterResult("succeeded", {"dry_run": True}))
            self.started[aid] = entry
            self.journal.append(entry)  # write-ahead: a crash after this is reconciled
            self._audit(entry, "action.started", f"{action['type']} on {action['target']} via {adapter.name}")
            try:
                result = adapter.execute(aid, action)
            except SystemExit:
                raise
            except Exception as exc:  # an adapter bug is a failed action, not a crash
                result = AdapterResult("failed", error=f"{type(exc).__name__}: {exc}"[:300])
            if result.status == "started":
                entry["async"] = True
                return self._record(entry, "started", result)
            return self._finish(entry, result)

    def complete(self, action_id: str, result: AdapterResult) -> dict[str, Any]:
        """Outcome of an asynchronous action."""
        with self.lock:
            entry = self.started.get(action_id)
            if entry is None:
                raise Reject("unknown action", action_id)
            return self._finish(entry, result)

    # -- checks

    def _check(self, req: Mapping[str, Any]) -> tuple[dict[str, Any], bool, set[str]]:
        if self.suspended():
            raise Reject("policy.suspended", "the owner has suspended actions on this cell")
        if not isinstance(req, Mapping):
            raise Reject("validation.malformed", "not an object")
        for k in ("action_id", "proposal", "decision", "idempotency_key"):
            if k not in req:
                raise Reject("validation.malformed", f"missing {k}")
        proposal, decision = req["proposal"], req["decision"]
        if not isinstance(proposal, Mapping) or not isinstance(decision, Mapping):
            raise Reject("validation.malformed", "proposal and decision must be objects")
        action = proposal.get("action")
        if not isinstance(action, Mapping) or not {"type", "target", "params"} <= set(action):
            raise Reject("validation.malformed", "proposal.action")
        if req["idempotency_key"] != proposal.get("idempotency_key"):
            raise Reject("validation.binding", "idempotency_key differs from the proposal's")
        if decision.get("proposal_id") != proposal.get("proposal_id"):
            raise Reject("validation.binding", "the decision is for another proposal")
        digest = params_hash(dict(action))
        if decision.get("params_hash") != digest:
            raise Reject("validation.binding", "the decision does not cover these parameters")
        try:
            verify_object(decision, "decision", self.pdp_keys)
        except Reject as why:
            raise Reject("validation.decision_signature", why.reason) from None
        now = self.clock()
        if _time(decision.get("expires")) <= now:
            raise Reject("validation.expired", f"decision expired at {decision.get('expires')}")
        effect = decision.get("effect")
        if effect == "deny":
            raise Reject("policy.denied", str(decision.get("reason", "")))
        obligations = {str(o.get("type")) for o in decision.get("obligations", []) if isinstance(o, Mapping)}
        unknown = obligations - KNOWN_OBLIGATIONS
        if unknown:
            raise Reject("validation.obligation", f"unknown obligation {sorted(unknown)[0]}")
        if effect == "require_approval" or "approval" in obligations:
            self._check_approval(req.get("approval"), decision, now)
        elif effect != "permit":
            raise Reject("validation.malformed", f"decision effect {effect!r}")
        if action["type"] not in self.adapters:
            raise Reject("validation.capability", f"no adapter for {action['type']}")
        principal = str(proposal.get("proposer", ""))
        automatic = not principal.startswith("owner:")
        self._check_limits(decision, action, automatic, now)
        return dict(action), automatic, obligations

    def _check_approval(self, approval: Any, decision: Mapping[str, Any], now: float) -> None:
        if not isinstance(approval, Mapping):
            raise Reject("policy.requires_approval", "the decision needs an owner approval")
        if approval.get("decision_id") != decision.get("decision_id"):
            raise Reject("validation.binding", "the approval is for another decision")
        if approval.get("params_hash") != decision.get("params_hash"):
            raise Reject("validation.binding", "the approval is for other parameters")
        if _time(approval.get("expires")) <= now:
            raise Reject("validation.expired", f"approval expired at {approval.get('expires')}")
        wanted = [
            o.get("approver")
            for o in decision.get("obligations", [])
            if isinstance(o, Mapping) and o.get("type") == "approval"
        ]
        approver = str(approval.get("approver", ""))
        if wanted and approver not in wanted and approver != "owner:root":
            raise Reject("validation.approver", f"{approver} may not approve this")
        try:
            verify_owner_signed(approval, approver, self.registry, now)
        except Reject as why:
            raise Reject("validation.approval_signature", why.reason) from None

    def _check_limits(
        self, decision: Mapping[str, Any], action: Mapping[str, Any], automatic: bool, now: float
    ) -> None:
        day = [
            e
            for e in self.history(now - 86400)
            if self.records.get(e["action_id"], {}).get("status") != "rejected"
        ]
        for o in decision.get("obligations", []):
            if not isinstance(o, Mapping) or o.get("type") != "rate_limit":
                continue
            same = [
                e
                for e in day
                if e["action"]["type"] == action["type"] and e["action"]["target"] == action["target"]
            ]
            if "max_per_day" in o and len(same) >= int(o["max_per_day"]):
                raise Reject("policy.rate_limited", f"{len(same)} today (max {o['max_per_day']})")
            hour = [e for e in same if e["at"] >= now - 3600]
            if "max_per_hour" in o and len(hour) >= int(o["max_per_hour"]):
                raise Reject("policy.rate_limited", f"{len(hour)} this hour (max {o['max_per_hour']})")
        if not automatic:
            return
        target = action["target"]
        auto = [e for e in day if e.get("automatic") and e["action"]["target"] == target]
        if len([e for e in auto if e["at"] >= now - 3600]) >= self.auto_per_target_hour:
            raise Reject(
                "policy.rate_limited",
                f"already {self.auto_per_target_hour} automatic actions on {target} this hour",
            )
        failures = [
            e
            for e in self.history(now - self.breaker_hold)
            if e.get("automatic")
            and e["action"]["target"] == target
            and self.records.get(e["action_id"], {}).get("status") in ("failed", "unknown")
        ]
        if failures:
            raise Reject(
                "policy.circuit_open", f"an automatic action on {target} failed; held until the owner looks"
            )

    # -- records

    def _sign(self, rec: dict[str, Any]) -> dict[str, Any]:
        rec["executor"] = self.executor
        return sign_object(rec, "execution-record", self.key)

    def _reject(self, aid: str, req: Any, why: Reject) -> dict[str, Any]:
        code = why.reason if "." in why.reason else f"validation.{why.reason.replace(' ', '_')}"
        retryable = code.startswith(("unavailable.", "conflict."))
        rec = self._sign(
            {
                "action_id": aid if aid.startswith("act_") else "act_invalid",
                "status": "rejected",
                "error": {"code": code, "message": why.detail or why.reason, "retryable": retryable},
            }
        )
        log.warning("gateway: rejected %s: %s", aid, why)
        entry = {
            "action_id": rec["action_id"],
            "refs": _refs(req) if isinstance(req, Mapping) else {},
            "correlationid": str(req.get("correlationid", "corr_rejected"))
            if isinstance(req, Mapping)
            else "corr_rejected",
        }
        self._audit(entry, "action.rejected", f"rejected: {code} {why.detail}"[:1000])
        return rec

    def _record(
        self, entry: dict[str, Any], status: str, result: AdapterResult, reconciled: bool = False
    ) -> dict[str, Any]:
        now = self.clock()
        rec: dict[str, Any] = {
            "action_id": entry["action_id"],
            "status": status,
            "adapter": entry["adapter"],
            "started_at": to_rfc3339(float(entry["at"])),
        }
        if status in ("succeeded", "failed"):
            rec["finished_at"] = to_rfc3339(max(now, float(entry["at"])))
        if result.response:
            rec["adapter_response"] = result.response
        if result.before is not None:
            rec["before"] = result.before
        if result.after is not None:
            rec["after_reported"] = result.after
        rec["verification_pending"] = bool(entry.get("verify")) and status == "succeeded"
        if status == "failed" or status == "unknown":
            rec["error"] = {
                "code": "unavailable.adapter" if status == "failed" else "internal.unknown_outcome",
                "message": result.error or status,
                "retryable": False,
            }
        if reconciled:
            rec["reconciled"] = True
        rec = self._sign(rec)
        self.records[entry["action_id"]] = rec
        self.journal.append(
            {
                "event": status,
                "action_id": entry["action_id"],
                "at": now,
                "idempotency_key": entry.get("idempotency_key"),
                "record": rec,
                **({"async": True} if entry.get("async") else {}),
            }
        )
        return rec

    def _finish(
        self, entry: dict[str, Any], result: AdapterResult, reconciled: bool = False
    ) -> dict[str, Any]:
        status = result.status if result.status in ("succeeded", "failed", "unknown") else "failed"
        rec = self._record(entry, status, result, reconciled)
        self.started.pop(entry["action_id"], None)
        a = entry["action"]
        note = " (after a restart)" if reconciled else ""
        self._audit(
            entry,
            f"action.{status}",
            f"{a['type']} on {a['target']}: {status}{note} {result.error}".strip()[:1000],
        )
        return rec

    def _audit(self, entry: Mapping[str, Any], kind: str, summary: str) -> None:
        if self.audit is None:
            return
        refs = {k: v for k, v in dict(entry.get("refs", {})).items() if v}
        refs["action"] = str(entry["action_id"])
        try:
            self.audit.append(
                kind,
                summary=summary,
                correlationid=str(entry.get("correlationid") or "corr_unknown"),
                refs=refs,
            )
        except OSError as exc:
            log.error("gateway: audit append failed: %s", exc)


def _refs(req: Mapping[str, Any]) -> dict[str, str]:
    refs: dict[str, str] = {}
    for name, field_ in (
        ("proposal", "proposal_id"),
        ("decision", "decision_id"),
        ("approval", "approval_id"),
    ):
        obj = req.get(name)
        if isinstance(obj, Mapping) and isinstance(obj.get(field_), str):
            refs[name] = obj[field_]
    return refs


def _time(text: Any) -> float:
    try:
        return parse_rfc3339(str(text))
    except ValueError:
        return 0.0
