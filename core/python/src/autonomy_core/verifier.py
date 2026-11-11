"""Verifier (C14): did the action's expected effect actually happen?

An ExecutionRecord ``succeeded`` only means the adapter accepted the call (contracts §8.7).
``Verifier.check(proposal, record)`` reads observations through the probe the proposal
declared and returns a signed Verification (spec verification.schema.json):

==================  ====================================================================
result              when
==================  ====================================================================
``confirmed``       an observation made after the action started shows the expected effect
``contradicted``    the window (``expected_effect.within_s``) is over and the observations
                    made since the start all show something else (ST-12: "success" but no
                    flow)
``not_confirmed``   the window is over and the probe saw nothing since the start
``inconclusive``    the effect kind or the probe is unknown, the probe failed, or the
                    action did not succeed
==================  ====================================================================

Before the window closes and without a match ``check`` returns ``None`` (ask again later),
unless ``final=True``. Observations made before the action started are never evidence.

``independent`` is the verifier's own knowledge of the probe (``Probe.independent``: it does
not read through the channel the action wrote through), not the proposer's claim.

A ``contradicted`` result is audited as ``action.verified`` and ``incident.opened``, and
``on_contradicted`` gets the verification and an undo proposal (``undo_proposal``), if the
action declared one, for the owner to decide on.
"""

from __future__ import annotations

import contextlib
import json
import math
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import Reject
from .ids import parse_rfc3339, prefixed_id, to_rfc3339
from .keys import Ed25519PrivateKey
from .signing import sign_object

DEFAULT_WINDOW_S = 600


@dataclass
class Observation:
    ref: str  # e.g. evt:..., or probe:<name>:<entity>
    value: Any
    observed_at: float
    path: str  # dotted, e.g. ha.state, host.boot_time

    def as_dict(self) -> dict[str, Any]:
        return {
            "ref": self.ref,
            "value": self.value,
            "observed_at": to_rfc3339(self.observed_at),
            "path": self.path,
        }


@dataclass
class Probe:
    """``read(effect, action, since)`` returns observations of ``effect["entity"]`` (or of
    the action's target) made at or after ``since``. ``independent``: the probe does not read
    through the channel the action wrote through."""

    read: Callable[[Mapping[str, Any], Mapping[str, Any], float], list[Observation]]
    independent: bool = False


# kind -> matcher(effect, value, started_at) -> bool
def _equals(effect: Mapping[str, Any], value: Any, start: float) -> bool:
    want: Any = effect.get("value")
    if _num(want) and _num(value):
        tol = float(effect.get("tolerance", 0) or 0)
        return math.isclose(float(value), float(want), abs_tol=tol)
    return bool(value == want)


def _within(effect: Mapping[str, Any], value: Any, start: float) -> bool:
    if not _num(value):
        return False
    lo, hi = effect.get("min"), effect.get("max")
    return (lo is None or float(value) >= float(lo)) and (hi is None or float(value) <= float(hi))


def _restarted_after(effect: Mapping[str, Any], value: Any, start: float) -> bool:
    """value: when the entity last started (epoch seconds or RFC 3339); a restart after the
    action started (allowing ``skew_s``, default 5 s) is the effect."""
    try:
        t = float(value) if _num(value) else parse_rfc3339(str(value))
    except ValueError:
        return False
    return t >= start - float(effect.get("skew_s", 5))


MATCHERS: dict[str, Callable[[Mapping[str, Any], Any, float], bool]] = {
    "state_equals": _equals,
    "state_within": _within,
    "restarted_after": _restarted_after,
}


def _num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


class Verifier:
    """C14 for one cell. ``probes`` by name (proposal ``verification.probe``)."""

    def __init__(
        self,
        *,
        verifier: str,
        key: Ed25519PrivateKey,
        probes: Mapping[str, Probe],
        state_dir: Path | str | None = None,
        clock: Callable[[], float] = time.time,
        audit: Any = None,
        tracer: Any = None,
        on_contradicted: Callable[[dict[str, Any], dict[str, Any] | None], None] | None = None,
    ):
        self.verifier = verifier
        self.key = key
        self.probes = dict(probes)
        self.clock = clock
        self.audit = audit
        self.tracer = tracer
        self.on_contradicted = on_contradicted
        self.lock = threading.Lock()
        self.path = Path(state_dir) / "verifications.jsonl" if state_dir is not None else None
        self.done: dict[str, dict[str, Any]] = {}  # action_id -> verification
        if self.path is not None and self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                with contextlib.suppress(ValueError):
                    v = json.loads(line)
                    self.done[str(v["action_id"])] = v

    def verification(self, action_id: str) -> dict[str, Any] | None:
        return self.done.get(action_id)

    def check(
        self,
        proposal: Mapping[str, Any],
        record: Mapping[str, Any],
        *,
        final: bool = False,
        correlationid: str | None = None,
        refs: Mapping[str, str] | None = None,
        traceparent: str | None = None,
    ) -> dict[str, Any] | None:
        aid = str(record.get("action_id", ""))
        if not aid.startswith("act_"):
            raise Reject("malformed", "record has no action_id")
        if aid in self.done:
            return self.done[aid]
        span = (
            self.tracer.span("verifier.check", traceparent, **{"autonomy.action_id": aid})
            if self.tracer is not None
            else contextlib.nullcontext()
        )
        with span as sp:  # audit records written here carry the action's trace id (IT-71)
            v = self._check(proposal, record, final)
            if sp is not None:
                sp.set(**{"autonomy.verification": v["result"] if v else "pending"})
            if v is not None:
                self._settle(v, proposal, record, correlationid, refs)
        return v

    def check_pending(self, gateway: Any, *, final: bool = False) -> list[dict[str, Any]]:
        """Check every action the gateway is waiting on (``awaiting_verification``)."""
        out = []
        for item in gateway.awaiting_verification(self.done):
            v = self.check(
                item["proposal"],
                item["record"],
                final=final,
                correlationid=item.get("correlationid"),
                refs=item.get("refs"),
                traceparent=item.get("traceparent"),
            )
            if v is not None:
                out.append(v)
        return out

    # --

    def _check(
        self, proposal: Mapping[str, Any], record: Mapping[str, Any], final: bool
    ) -> dict[str, Any] | None:
        now = self.clock()
        aid = str(record["action_id"])
        if record.get("status") != "succeeded":
            return self._make(
                aid, "inconclusive", [], False, f"action {record.get('status')}: nothing to verify"
            )
        effect = proposal.get("expected_effect") or {}
        decl = proposal.get("verification") or {}
        action = proposal.get("action") or {}
        kind = str(effect.get("kind", ""))
        probe = self.probes.get(str(decl.get("probe", "")))
        matcher = MATCHERS.get(kind)
        if probe is None or matcher is None:
            what = f"probe {decl.get('probe')!r}" if probe is None else f"effect kind {kind!r}"
            return self._make(aid, "inconclusive", [], False, f"no verifier for {what}")
        try:
            start = parse_rfc3339(str(record.get("started_at")))
        except ValueError:
            return self._make(aid, "inconclusive", [], probe.independent, "record has no started_at")
        deadline = start + float(effect.get("within_s") or DEFAULT_WINDOW_S)
        try:
            seen = [o for o in probe.read(effect, action, start) if o.observed_at >= start]
        except Exception as exc:  # a broken probe proves nothing
            if now < deadline and not final:
                return None
            return self._make(
                aid, "inconclusive", [], probe.independent, f"probe failed: {type(exc).__name__}: {exc}"
            )
        seen.sort(key=lambda o: o.observed_at)
        hits = [o for o in seen if matcher(effect, o.value, start)]
        if hits:
            return self._make(aid, "confirmed", hits[-1:], probe.independent, "")
        if now < deadline and not final:
            return None
        if seen:
            last = seen[-1]
            return self._make(
                aid,
                "contradicted",
                seen[-3:],
                probe.independent,
                f"expected {_describe(effect)}; observed {last.value!r}",
            )
        return self._make(
            aid, "not_confirmed", [], probe.independent, f"no observation within {int(deadline - start)} s"
        )

    def _make(
        self, aid: str, result: str, obs: list[Observation], independent: bool, note: str
    ) -> dict[str, Any]:
        now = self.clock()
        v: dict[str, Any] = {
            "verification_id": prefixed_id("ver", now),
            "action_id": aid,
            "result": result,
            "observations": [o.as_dict() for o in obs],
            "independent": bool(independent and obs),
            "verified_at": to_rfc3339(now),
            "verifier": self.verifier,
        }
        if note:
            v["note"] = note[:2000]
        return sign_object(v, "verification", self.key)

    def _settle(
        self,
        v: dict[str, Any],
        proposal: Mapping[str, Any],
        record: Mapping[str, Any],
        corr: str | None,
        refs: Mapping[str, str] | None,
    ) -> None:
        aid = v["action_id"]
        with self.lock:
            self.done[aid] = v
            if self.path is not None:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(v, separators=(",", ":")) + "\n")
                    f.flush()
        action = proposal.get("action") or {}
        r = {k: str(val) for k, val in dict(refs or {}).items() if val}
        if proposal.get("proposal_id"):
            r.setdefault("proposal", str(proposal["proposal_id"]))
        r.update(action=aid, verification=v["verification_id"])
        correlationid = corr or f"corr_{aid[4:]}"
        if self.audit is not None:
            how = "independently" if v["independent"] else "via the same channel"
            self.audit.append(
                "action.verified",
                summary=f"{v['result']} ({how}): {action.get('type')} on {action.get('target')}"
                + (f"; {v['note']}" if v.get("note") else ""),
                correlationid=correlationid,
                refs=r,
            )
        if v["result"] != "contradicted":
            return
        undo = undo_proposal(proposal, record, self.clock(), self.verifier)
        if self.audit is not None:
            self.audit.append(
                "incident.opened",
                summary=(
                    f"contradicted outcome of {action.get('type')} on {action.get('target')}: "
                    + str(v.get("note", ""))
                    + (f"; undo proposed as {undo['proposal_id']}" if undo else "; no undo declared")
                )[:1000],
                correlationid=correlationid,
                refs=r,
            )
        if self.on_contradicted is not None:
            try:
                self.on_contradicted(v, undo)
            except Exception:  # noqa: BLE001 - the verification is already recorded
                import logging

                logging.getLogger("autonomy.verifier").exception("on_contradicted failed")


def undo_proposal(
    proposal: Mapping[str, Any], record: Mapping[str, Any], now: float, proposer: str = "svc:verifier"
) -> dict[str, Any] | None:
    """A new proposal for the declared undo, with ``"previous"`` replaced by the value
    before execution (``record.before``); None for irreversible actions or no before value."""
    rev = proposal.get("reversibility") or {}
    undo = rev.get("undo")
    action = proposal.get("action") or {}
    if not isinstance(undo, Mapping):
        return None
    before = record.get("before")
    params: dict[str, Any] = {}
    for k, val in dict(undo.get("params") or {}).items():
        if val == "previous":
            if isinstance(before, Mapping) and k in before:
                val = before[k]
            elif before is not None and not isinstance(before, Mapping):
                val = before
            else:
                return None  # cannot undo without knowing the previous value
        params[k] = val
    pid = str(proposal.get("proposal_id", ""))
    aid = str(record.get("action_id", ""))
    return {
        "proposal_id": prefixed_id("prp", now),
        "proposer": proposer,
        "objective": {
            "id": "obj_undo_contradicted",
            "text": f"Undo {action.get('type')} {aid}: outcome contradicted",
        },
        "action": {
            "type": undo["type"],
            "target": undo.get("target", action.get("target")),
            "params": params,
        },
        "rationale": f"verification contradicted the expected effect of {aid} ({pid})",
        "inputs": [{"ref": f"act:{aid}", "trust": "trusted", "observed_at": to_rfc3339(now)}],
        "expected_effect": (
            {
                "kind": "state_equals",
                "entity": action.get("target"),
                "value": params.get("value"),
                "within_s": 300,
            }
            if "value" in params
            else {"kind": "unknown"}
        ),
        "verification": dict(proposal.get("verification") or {"probe": "unknown", "independent": False}),
        "reversibility": {"irreversible": True, "note": "undo of an undo is a new proposal"},
        "freshness_s": 3600,
        "idempotency_key": f"undo:{aid}",
    }


def _describe(effect: Mapping[str, Any]) -> str:
    kind = effect.get("kind")
    if kind == "state_equals":
        return f"{effect.get('entity')} = {effect.get('value')!r}"
    if kind == "state_within":
        return f"{effect.get('entity')} in [{effect.get('min')}, {effect.get('max')}]"
    if kind == "restarted_after":
        return f"{effect.get('entity')} restarted after the action"
    return str(kind)
