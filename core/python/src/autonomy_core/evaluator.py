"""Evaluator (C13): should this proposal go ahead? Deterministic checks first; an optional
LLM review afterwards that can only make the outcome stricter.

``Evaluator.evaluate(proposal, risk_class=...)`` returns an Evaluation (spec
evaluation.schema.json) whose ``outcome`` goes into the DecisionRequest as
``context.evaluation``; the PDP denies ``reject`` and turns ``escalate`` into
``require_approval`` (design/governance-and-authority.md, ADR-008).

Checks, in order:

=====================  ===============================================================  ========
check                  fails when                                                       outcome
=====================  ===============================================================  ========
``schema``             a required proposal field is missing or the wrong type           reject
``inputs_fresh``       a dated input is older than ``freshness_s`` or in the future     reject
                       all inputs are undated (freshness cannot be shown)               escalate
``inputs_trusted``     an input is ``untrusted``                                        escalate
``invariant.<name>``   an invariant registered for the action type fails                reject
``conflicts``          another open proposal acts on the same target differently        escalate
``verifiable``         the effect kind or probe is unknown to the verifier (R1+)        escalate
``reversible``         the action is irreversible at R2 or above                        escalate
``llm_review``         the reviewer objects, errors, or answers something unknown       escalate
=====================  ===============================================================  ========

Stale inputs are never ``ok`` (M8 acceptance). The reviewer is advisory: it runs only when
the deterministic outcome is not ``reject``, and whatever it says, the outcome can move from
``ok`` to ``escalate`` and never back (an LLM never approves and never silently drops a
proposal; the owner sees what it objected to).
"""

from __future__ import annotations

import contextlib
import fnmatch
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from .ids import parse_rfc3339, prefixed_id, to_rfc3339
from .jcs import canonicalize

RISK = ("R0", "R1", "R2", "R3")

REQUIRED: dict[str, type | tuple[type, ...]] = {
    "proposal_id": str,
    "proposer": str,
    "objective": dict,
    "action": dict,
    "rationale": str,
    "inputs": list,
    "expected_effect": dict,
    "verification": dict,
    "reversibility": dict,
    "freshness_s": int,
    "idempotency_key": str,
}

# Effect kinds the core Verifier understands (verifier.MATCHERS); other kinds are inconclusive.
KNOWN_EFFECTS = {"state_equals", "state_within", "restarted_after"}


@dataclass
class Check:
    name: str
    ok: bool
    note: str = ""
    advisory: bool = False
    model: str | None = None
    severity: str = "reject"  # what a failure means: "reject" or "escalate"

    def as_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"name": self.name, "ok": self.ok}
        if self.advisory:
            d["advisory"] = True
        if self.model:
            d["model"] = self.model
        if self.note:
            d["note"] = self.note[:2000]
        return d


@dataclass
class Review:
    """What an LLM (or any non-deterministic) reviewer said. ``verdict``: ok, escalate, reject."""

    verdict: str
    note: str = ""
    model: str | None = None


class Reviewer(Protocol):
    def __call__(self, proposal: Mapping[str, Any], checks: list[dict[str, Any]]) -> Review: ...


# An invariant gets the proposal and returns (ok, note).
Invariant = Callable[[Mapping[str, Any]], "tuple[bool, str]"]


def value_range(param: str, lo: float | None = None, hi: float | None = None) -> Invariant:
    """``action.params[param]`` is a number within [lo, hi]."""

    def check(proposal: Mapping[str, Any]) -> tuple[bool, str]:
        v = proposal.get("action", {}).get("params", {}).get(param)
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return False, f"{param} is not a number"
        if lo is not None and v < lo or hi is not None and v > hi:
            return False, f"{param}={v} outside [{lo}, {hi}]"
        return True, f"{param}={v} within [{lo}, {hi}]"

    return check


def allowed_targets(*patterns: str) -> Invariant:
    """``action.target`` matches one of the glob patterns."""

    def check(proposal: Mapping[str, Any]) -> tuple[bool, str]:
        target = str(proposal.get("action", {}).get("target", ""))
        if any(fnmatch.fnmatchcase(target, p) for p in patterns):
            return True, ""
        return False, f"target {target} not in {', '.join(patterns)}"

    return check


@dataclass
class Evaluator:
    """C13. ``invariants`` maps an action-type glob to named invariants, e.g.
    ``{"ha.helper.set": {"value_range": value_range("value", 50, 150)}}``.
    ``open_proposals`` returns the proposals still in flight (for conflict checks);
    ``probes`` is the set of probe names the verifier has (None: do not check)."""

    invariants: Mapping[str, Mapping[str, Invariant]] = field(default_factory=dict)
    open_proposals: Callable[[], Iterable[Mapping[str, Any]]] = lambda: ()
    probes: set[str] | None = None
    effects: set[str] = field(default_factory=lambda: set(KNOWN_EFFECTS))
    reviewer: Reviewer | None = None
    clock: Callable[[], float] = time.time
    max_skew_s: float = 60.0
    audit: Any = None  # AuditLog: each evaluation is recorded as action.evaluated
    tracer: Any = None

    def evaluate(
        self, proposal: Mapping[str, Any], *, risk_class: str, correlationid: str | None = None
    ) -> dict[str, Any]:
        span = self.tracer.span("evaluator.evaluate") if self.tracer is not None else contextlib.nullcontext()
        with span as sp:
            ev = self._evaluate(proposal, risk_class)
            if sp is not None:
                sp.set(**{"autonomy.evaluation": ev["outcome"]})
        if self.audit is not None:
            pid = ev["proposal_id"]
            act = proposal.get("action", {}) if isinstance(proposal, Mapping) else {}
            failed = [c["name"] for c in ev["checks"] if not c["ok"]]
            self.audit.append(
                "action.evaluated",
                summary=(
                    f"{ev['outcome']}: {act.get('type')} on {act.get('target')}"
                    + (f" (failed: {', '.join(failed)})" if failed else "")
                    + (f"; {'; '.join(ev['escalation_reasons'])}" if ev["escalation_reasons"] else "")
                )[:1000],
                correlationid=correlationid
                or (f"corr_{pid[4:]}" if pid.startswith("prp_") else "corr_unknown"),
                refs={"proposal": pid, "evaluation": ev["evaluation_id"]},
            )
        return ev

    # -- the checks

    def _evaluate(self, p: Mapping[str, Any], risk: str) -> dict[str, Any]:
        now = self.clock()
        if risk not in RISK:
            risk = "R3"
        checks: list[Check] = [self._schema(p)]
        if checks[0].ok:
            checks += [self._fresh(p, now), self._trusted(p)]
            checks += self._invariants(p)
            checks += [self._conflicts(p), self._verifiable(p, risk), self._reversible(p, risk)]
        outcome, reasons = _outcome(checks)
        pid = p.get("proposal_id") if isinstance(p, Mapping) else None
        pid = str(pid) if _is_pid(pid) else "prp_invalid"
        if outcome != "reject" and self.reviewer is not None:
            review = self._review(p, checks)
            checks.append(review)
            if not review.ok:
                outcome = "escalate"
                reasons.append(f"llm_review: {review.note or 'objected'}"[:500])
        return {
            "evaluation_id": prefixed_id("evl", now),
            "proposal_id": pid,
            "risk_class": risk,
            "checks": [c.as_dict() for c in checks],
            "outcome": outcome,
            "escalation_reasons": reasons,
            "evaluated_at": to_rfc3339(now),
        }

    def _schema(self, p: Mapping[str, Any]) -> Check:
        if not isinstance(p, Mapping):
            return Check("schema", False, "proposal is not an object")
        problems = [
            k
            for k, t in REQUIRED.items()
            if not isinstance(p.get(k), t) or (t is int and isinstance(p.get(k), bool))
        ]
        a = p.get("action")
        if isinstance(a, Mapping) and not (
            isinstance(a.get("type"), str)
            and isinstance(a.get("target"), str)
            and isinstance(a.get("params"), dict)
        ):
            problems.append("action.type/target/params")
        if isinstance(p.get("freshness_s"), int) and p["freshness_s"] < 1:
            problems.append("freshness_s < 1")
        if not _is_pid(p.get("proposal_id")):
            problems.append("proposal_id format")
        return Check("schema", not problems, ("bad: " + ", ".join(problems)) if problems else "")

    def _fresh(self, p: Mapping[str, Any], now: float) -> Check:
        limit = float(p["freshness_s"])
        stale, undated, dated = [], [], 0
        for i in p["inputs"]:
            ref = str(i.get("ref", "?")) if isinstance(i, Mapping) else "?"
            at = i.get("observed_at") if isinstance(i, Mapping) else None
            if at is None:
                undated.append(ref)
                continue
            try:
                t = parse_rfc3339(str(at))
            except ValueError:
                stale.append(f"{ref} (bad time)")
                continue
            dated += 1
            age = now - t
            if age > limit:
                stale.append(f"{ref} is {int(age)} s old (limit {int(limit)} s)")
            elif age < -self.max_skew_s:
                stale.append(f"{ref} is from the future")
        if stale:
            return Check("inputs_fresh", False, "; ".join(stale))
        if undated and not dated:
            return Check("inputs_fresh", False, "no input has observed_at", severity="escalate")
        return Check("inputs_fresh", True, f"{dated} dated input(s) within {int(limit)} s" if dated else "")

    def _trusted(self, p: Mapping[str, Any]) -> Check:
        bad = [
            str(i.get("ref")) for i in p["inputs"] if isinstance(i, Mapping) and i.get("trust") == "untrusted"
        ]
        return Check(
            "inputs_trusted", not bad, ("untrusted: " + ", ".join(bad)) if bad else "", severity="escalate"
        )

    def _invariants(self, p: Mapping[str, Any]) -> list[Check]:
        out = []
        atype = p["action"]["type"]
        for pattern, named in self.invariants.items():
            if not fnmatch.fnmatchcase(atype, pattern):
                continue
            for name, inv in named.items():
                try:
                    ok, note = inv(p)
                except Exception as exc:  # a broken invariant fails closed
                    ok, note = False, f"invariant error: {type(exc).__name__}: {exc}"
                out.append(Check(f"invariant.{name}", bool(ok), note))
        return out

    def _conflicts(self, p: Mapping[str, Any]) -> Check:
        mine = p["action"]
        clashes = []
        try:
            others = list(self.open_proposals())
        except Exception as exc:  # cannot tell: the owner decides
            return Check("conflicts", False, f"cannot list open proposals: {exc}", severity="escalate")
        for o in others:
            if not isinstance(o, Mapping) or o.get("proposal_id") == p.get("proposal_id"):
                continue
            a = o.get("action", {})
            if a.get("target") == mine["target"] and canonicalize(dict(a)) != canonicalize(dict(mine)):
                clashes.append(f"{o.get('proposal_id')} ({a.get('type')})")
        return Check(
            "conflicts",
            not clashes,
            ("same target: " + ", ".join(clashes)) if clashes else "",
            severity="escalate",
        )

    def _verifiable(self, p: Mapping[str, Any], risk: str) -> Check:
        if risk == "R0":
            return Check("verifiable", True, "R0: nothing to verify")
        kind = str(p["expected_effect"].get("kind", ""))
        probe = str(p["verification"].get("probe", ""))
        problems = []
        if kind not in self.effects:
            problems.append(f"effect kind {kind!r} unknown to the verifier")
        if self.probes is not None and probe not in self.probes:
            problems.append(f"no probe {probe!r}")
        return Check("verifiable", not problems, "; ".join(problems), severity="escalate")

    def _reversible(self, p: Mapping[str, Any], risk: str) -> Check:
        irreversible = p["reversibility"].get("irreversible") is True
        if irreversible and RISK.index(risk) >= 2:
            return Check("reversible", False, "irreversible at " + risk, severity="escalate")
        return Check("reversible", True, "irreversible" if irreversible else "undo declared")

    def _review(self, p: Mapping[str, Any], checks: list[Check]) -> Check:
        assert self.reviewer is not None
        try:
            r = self.reviewer(p, [c.as_dict() for c in checks])
        except Exception as exc:  # unavailable reviewer: do not pretend it agreed
            return Check("llm_review", False, f"review unavailable: {type(exc).__name__}", advisory=True)
        verdict = getattr(r, "verdict", None)
        model = getattr(r, "model", None)
        note = str(getattr(r, "note", "") or "")
        if verdict == "ok":
            return Check("llm_review", True, note, advisory=True, model=model)
        if verdict not in ("escalate", "reject"):
            note = f"unknown verdict {verdict!r}"
        return Check("llm_review", False, note or f"reviewer said {verdict}", advisory=True, model=model)


def _outcome(checks: list[Check]) -> tuple[str, list[str]]:
    failed = [c for c in checks if not c.ok]
    if any(c.severity == "reject" and not c.advisory for c in failed):
        return "reject", []
    reasons = [f"{c.name}: {c.note or 'failed'}"[:500] for c in failed]
    return ("escalate" if reasons else "ok"), reasons


def _is_pid(v: Any) -> bool:
    import re

    return isinstance(v, str) and re.fullmatch(r"prp_[A-Za-z0-9_-]{1,64}", v) is not None
