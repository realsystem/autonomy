"""Policy Decision Point (C04): may this principal do this action on this resource now?

Inputs: a DecisionRequest (spec), the owner-signed policy bundle (grants + Cedar policies)
and the verified registry (owner keys that sign grants and bundles). Output: a signed
Decision bound to the exact action by ``params_hash``.

Rules, in order (design/governance-and-authority.md §2-4):

1. R3 is never permitted; an evaluation ``reject`` is a deny.
2. Cedar ``forbid`` wins over everything.
3. Something must permit: a matching grant, a Cedar ``permit``, the owner acting
   (``context.owner_verified``), or R0 observation.
4. Permitted actions still go to the owner (``require_approval``) when: derived from
   untrusted input at R1+, the evaluator escalated, or the grant's autonomy level does not
   cover the risk (A1/A2: every R1/R2; A3: R2; A4+: listed R1/R2 within bounds).
5. Nothing permits but an ``approvable`` Cedar policy would with a valid approval in
   context: ``require_approval``. Otherwise deny.

Cedar is evaluated with ``cedarpy`` (ADR-004). A bundle with Cedar policies on a host
without ``cedarpy`` fails closed: every decision is a deny.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from .errors import Reject
from .ids import parse_rfc3339, prefixed_id, to_rfc3339
from .jcs import canonicalize
from .keys import Ed25519PrivateKey, Ed25519PublicKey, decode_public_key, encode_public_key, verify
from .registry import Registry, same_key
from .signing import params_hash, sign_object

RISK = ("R0", "R1", "R2", "R3")
LEVELS = ("A0", "A1", "A2", "A3", "A4", "A5")
POWER_STAGES = ("shutdown", "critical", "low", "reduced", "normal")
BASELINE_VERSION = "baseline"
_BUNDLE_VERSION = re.compile(r"^(\d{4})\.(\d{2})\.(\d{2})-([1-9]\d*)$")
_STATEMENT = re.compile(r"(?m)^\s*(?:@[^\n]*\n\s*)*(permit|forbid)\s*\(")
_ENTITY_TYPES = {
    "cell": "Cell",
    "svc": "Service",
    "agent": "Agent",
    "adapter": "Adapter",
    "app": "App",
    "owner": "Owner",
}


def bundle_order(version: str) -> tuple[int, ...]:
    m = _BUNDLE_VERSION.match(version)
    if not m:
        raise Reject("malformed", f"bundle version {version!r}")
    return tuple(int(g) for g in m.groups())


def _risk_at_least(risk: str, floor: str) -> bool:
    return RISK.index(risk) >= RISK.index(floor)


def _owner_keys(registry: Registry | None, now: float) -> list[tuple[str, Ed25519PublicKey]]:
    if registry is None:
        return []
    owners = registry.doc.get("owners", {})
    keys = [("owner:root", decode_public_key(owners["root"]))]
    for op in owners.get("ops", []):
        try:
            if parse_rfc3339(op["expires"]) > now:
                keys.append(("owner:ops", decode_public_key(op["key"])))
        except (KeyError, ValueError):
            continue
    return keys


def verify_owner_signed(obj: Mapping[str, Any], signer: str, registry: Registry | None, now: float) -> None:
    """A grant (signed by ``issued_by``) or bundle (``signer`` + ``key``) signed by a current
    owner key of the registry. Raises Reject."""
    body = canonicalize({k: v for k, v in obj.items() if k != "signature"})
    sig = str(obj.get("signature", ""))
    for role, key in _owner_keys(registry, now):
        if role == signer and verify(key, body, sig):
            if "key" in obj and not same_key(str(obj["key"]), encode_public_key(key)):
                continue
            return
    raise Reject("bad signature", f"not signed by a current {signer} key")


@dataclass(frozen=True)
class Policy:
    id: str
    cedar: str
    approvable: bool = False


@dataclass(frozen=True)
class PolicyBundle:
    version: str
    policies: tuple[Policy, ...] = ()
    grants: tuple[dict[str, Any], ...] = ()
    groups: dict[str, list[str]] = field(default_factory=dict)
    signer: str = ""

    @classmethod
    def baseline(cls, policies: Iterable[Policy] = ()) -> PolicyBundle:
        """Policies shipped with the software (not owner-signed); grants come only from
        signed bundles."""
        return cls(BASELINE_VERSION, tuple(policies))


def load_bundle(
    doc: Mapping[str, Any],
    registry: Registry | None,
    *,
    active: PolicyBundle | None = None,
    now: float | None = None,
) -> PolicyBundle:
    """Verify an owner-signed bundle (spec policy-bundle). Raises Reject."""
    t = time.time() if now is None else now
    for k in ("bundle", "issued_at", "policies", "grants", "signer", "key", "signature"):
        if k not in doc:
            raise Reject("malformed", f"bundle has no {k}")
    version = str(doc["bundle"])
    order = bundle_order(version)
    signer = str(doc["signer"])
    verify_owner_signed(doc, signer, registry, t)
    if active is not None and active.version != BASELINE_VERSION:
        if order < bundle_order(active.version) and signer != "owner:root":
            raise Reject("rollback", f"bundle {version} is older than {active.version}")
    policies = []
    for p in doc["policies"]:
        if len(_STATEMENT.findall(str(p.get("cedar", "")))) != 1:
            raise Reject("malformed", f"policy {p.get('id')} must hold exactly one statement")
        policies.append(Policy(str(p["id"]), str(p["cedar"]), bool(p.get("approvable", False))))
    grants = []
    for g in doc["grants"]:
        verify_owner_signed(g, str(g.get("issued_by", "")), registry, t)
        grants.append(dict(g))
    groups = {str(k): [str(x) for x in v] for k, v in dict(doc.get("groups", {})).items()}
    return PolicyBundle(version, tuple(policies), tuple(grants), groups, signer)


# --- Cedar ----------------------------------------------------------------------------


class CedarUnavailable(Exception):
    pass


CedarEngine = Callable[[dict[str, Any], str, list[dict[str, Any]]], tuple[bool, list[str], list[str]]]


def cedarpy_engine() -> CedarEngine:
    """Evaluate with cedarpy: returns (allowed, determining policy ids, errors)."""
    try:
        import cedarpy  # type: ignore[import-not-found, unused-ignore]
    except ImportError as exc:
        raise CedarUnavailable("cedarpy is not installed") from exc

    def evaluate(
        request: dict[str, Any], policies: str, entities: list[dict[str, Any]]
    ) -> tuple[bool, list[str], list[str]]:
        result = cedarpy.is_authorized(request, policies, entities)
        diag = getattr(result, "diagnostics", None)
        reasons = [str(r) for r in (getattr(diag, "reasons", None) or [])]
        errors = [str(e) for e in (getattr(diag, "errors", None) or [])]
        return bool(result.allowed), reasons, errors

    return evaluate


def entity_uid(ref: str, default_type: str) -> str:
    """Cedar entity uid for a component reference or resource name."""
    kind, _, name = ref.partition(":")
    if kind in _ENTITY_TYPES and name:
        return f'{_ENTITY_TYPES[kind]}::"{name.split("@")[0]}"'
    return f"{default_type}::{_cedar_string(ref)}"


def _cedar_string(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _cedar_value(v: Any) -> Any:
    """Cedar has no floats or nulls: integral floats become Longs, others strings."""
    if isinstance(v, bool) or isinstance(v, (int, str)):
        return v
    if isinstance(v, float):
        return int(v) if v.is_integer() else str(v)
    if isinstance(v, dict):
        return {str(k): _cedar_value(x) for k, x in v.items() if x is not None}
    if isinstance(v, list):
        return [_cedar_value(x) for x in v if x is not None]
    return str(v)


# --- the PDP ----------------------------------------------------------------------------


@dataclass
class Outcome:
    effect: str
    matched: list[str]
    reason: str
    grant: dict[str, Any] | None = None


class Pdp:
    def __init__(
        self,
        *,
        issuer: str,
        key: Ed25519PrivateKey,
        bundle: PolicyBundle,
        cedar: CedarEngine | None = None,
        clock: Callable[[], float] = time.time,
        permit_ttl: float = 900.0,
        approval_ttl: float = 3600.0,
    ):
        self.issuer = issuer
        self.key = key
        self.bundle = bundle
        self.clock = clock
        self.permit_ttl = permit_ttl
        self.approval_ttl = approval_ttl
        self._cedar = cedar
        self._cedar_error = ""
        if cedar is None and bundle.policies:
            try:
                self._cedar = cedarpy_engine()
            except CedarUnavailable as exc:
                self._cedar_error = str(exc)

    # -- grants

    def matching_grant(self, req: Mapping[str, Any], now: float) -> tuple[dict[str, Any] | None, str]:
        """The first grant that covers this request, or (None, why the closest one did not)."""
        ctx = req["context"]
        principal = str(req["principal"])
        why = "no grant for this principal and action"
        for g in self.bundle.grants:
            grantee = str(g["grantee"])
            if grantee not in (principal, principal.split("@")[0]):
                continue
            if req["action"] not in g["actions"]:
                continue
            if not any(_matches(req["resource"], sel) for sels in g["resources"].values() for sel in sels):
                why = "resource not covered by the grant"
                continue
            if not parse_rfc3339(g["valid_from"]) <= now < parse_rfc3339(g["expires"]):
                why = f"grant {g['grant_id']} is not valid now"
                continue
            problem = _constraint_problem(g.get("constraints", {}), ctx)
            if problem:
                why = f"grant {g['grant_id']}: {problem}"
                continue
            return g, ""
        return None, why

    # -- Cedar

    def _cedar_eval(
        self, req: Mapping[str, Any], context: dict[str, Any]
    ) -> tuple[bool, list[str], list[str]]:
        if not self.bundle.policies:
            return False, [], []
        if self._cedar is None:
            raise Reject("policy engine unavailable", self._cedar_error)
        text = "\n".join(p.cedar for p in self.bundle.policies)
        entities: list[dict[str, Any]] = [
            {"uid": {"__entity": {"type": "Group", "id": g}}, "attrs": {}, "parents": []}
            for g in self.bundle.groups
        ]
        parents = [
            {"__entity": {"type": "Group", "id": g}}
            for g, members in self.bundle.groups.items()
            if req["resource"] in members
        ]
        entities.append(
            {
                "uid": {"__entity": {"type": "Resource", "id": str(req["resource"])}},
                "attrs": {},
                "parents": parents,
            }
        )
        request = {
            "principal": entity_uid(str(req["principal"]), "Principal"),
            "action": f"Action::{_cedar_string(str(req['action']))}",
            "resource": f"Resource::{_cedar_string(str(req['resource']))}",
            "context": _cedar_value(context),
        }
        allowed, reasons, errors = self._cedar(request, text, entities)
        ids = [self._policy_id(r) for r in reasons]
        return allowed, ids, errors

    def _policy_id(self, reason: str) -> str:
        m = re.fullmatch(r"policy(\d+)", reason)
        if m and int(m.group(1)) < len(self.bundle.policies):
            return self.bundle.policies[int(m.group(1))].id
        return reason

    # -- decide

    def evaluate(self, req: Mapping[str, Any]) -> Outcome:
        """The decision without signing (for tests and dry runs)."""
        now = self.clock()
        ctx = dict(req["context"])
        risk = str(ctx["risk_class"])
        if risk == "R3":
            return Outcome("deny", [], "R3 actions are never permitted remotely")
        if ctx.get("evaluation") == "reject":
            return Outcome("deny", [], "the evaluator rejected the proposal")
        grant, why_not = self.matching_grant(req, now)
        cedar_ctx = dict(ctx)
        if grant is not None:
            cedar_ctx["grant"] = {
                "id": grant["grant_id"],
                "autonomy_level": grant["autonomy_level"],
                "responsibility": grant["responsibility"],
                "constraints": grant.get("constraints", {}),
            }
        allowed, ids, errors = self._cedar_eval(req, cedar_ctx)
        if errors:
            return Outcome("deny", ids, "policy evaluation error: " + "; ".join(errors)[:300])
        if not allowed and ids:
            return Outcome("deny", [f"policy:{i}" for i in ids], "forbidden by policy " + ", ".join(ids))
        matched = [f"policy:{i}" for i in ids] if allowed else []
        owner = str(req["principal"]).startswith("owner:") and ctx.get("owner_verified") is True
        if grant is not None:
            matched.insert(0, str(grant["grant_id"]))
        if grant is None and not allowed and not owner:
            if risk == "R0":
                return Outcome("permit", matched, "R0 observation")
            approvable = self._approvable(req, cedar_ctx)
            if approvable:
                return Outcome("require_approval", approvable, "an owner approval would permit this")
            return Outcome(
                "deny", matched, why_not if self.bundle.grants else "no grant or policy permits this"
            )
        # permitted; does the owner still have to approve?
        if owner:
            return Outcome("permit", matched or ["owner"], "the owner asked for it", grant)
        if risk != "R0":
            if ctx.get("trust") == "untrusted":
                return Outcome("require_approval", matched, "derived from untrusted input", grant)
            if ctx.get("evaluation") == "escalate":
                return Outcome("require_approval", matched, "the evaluator escalated", grant)
            if grant is not None and not _level_covers(str(grant["autonomy_level"]), risk):
                return Outcome(
                    "require_approval",
                    matched,
                    f"level {grant['autonomy_level']} needs approval for {risk}",
                    grant,
                )
        return Outcome("permit", matched, "permitted", grant)

    def _approvable(self, req: Mapping[str, Any], ctx: dict[str, Any]) -> list[str]:
        approvable = {p.id for p in self.bundle.policies if p.approvable}
        if not approvable:
            return []
        allowed, ids, errors = self._cedar_eval(req, {**ctx, "approval": {"valid": True}})
        if allowed and not errors and ids and set(ids) <= approvable:
            return [f"policy:{i}" for i in ids]
        return []

    def decide(self, req: Mapping[str, Any], *, config_hash: str | None = None) -> dict[str, Any]:
        """A signed Decision (spec decision schema). Raises Reject on a malformed request."""
        _check_request(req)
        now = self.clock()
        try:
            out = self.evaluate(req)
        except Reject as why:  # engine unavailable: fail closed
            out = Outcome("deny", [], str(why))
        ctx = req["context"]
        action = {"type": req["action"], "target": req["resource"], "params": dict(ctx.get("params", {}))}
        obligations: list[dict[str, Any]] = []
        if out.effect != "deny":
            g = out.grant
            if (g is not None and g.get("requires_verification")) or ctx["risk_class"] != "R0":
                obligations.append({"type": "verify"})
            limits = (g or {}).get("constraints", {})
            for unit in ("max_per_day", "max_per_hour"):
                if unit in limits:
                    obligations.append(
                        {
                            "type": "rate_limit",
                            "key": f"{req['principal']}:{req['action']}:{req['resource']}",
                            unit: limits[unit],
                        }
                    )
            if out.effect == "require_approval":
                obligations.append({"type": "approval", "approver": "owner:ops"})
        ttl = self.approval_ttl if out.effect == "require_approval" else self.permit_ttl
        decision: dict[str, Any] = {
            "decision_id": prefixed_id("dec", now),
            "proposal_id": req["proposal_id"],
            "effect": out.effect,
            "matched": out.matched,
            "obligations": obligations,
            "params_hash": params_hash(action),
            "decided_at": to_rfc3339(now),
            "expires": to_rfc3339(now + ttl),
            "policy_bundle": self.bundle.version,
            "reason": out.reason[:1000],
            "issuer": self.issuer,
        }
        if config_hash:
            decision["config_hash"] = config_hash
        return sign_object(decision, "decision", self.key)


def _check_request(req: Mapping[str, Any]) -> None:
    if not isinstance(req, Mapping):
        raise Reject("malformed", "decision request")
    for k in ("proposal_id", "principal", "action", "resource", "context"):
        if k not in req:
            raise Reject("malformed", f"decision request has no {k}")
    ctx = req["context"]
    if not isinstance(ctx, Mapping) or ctx.get("risk_class") not in RISK:
        raise Reject("malformed", "context.risk_class")
    for k in ("trust", "cell", "now"):
        if k not in ctx:
            raise Reject("malformed", f"context has no {k}")


def _matches(resource: Any, selector: str) -> bool:
    r = str(resource)
    return r == selector or (selector.endswith("*") and r.startswith(selector[:-1]))


def _level_covers(level: str, risk: str) -> bool:
    """Whether a grant's autonomy level lets R1/R2 run without a per-action approval."""
    if level not in LEVELS:
        return False
    i = LEVELS.index(level)
    if risk == "R1":
        return i >= LEVELS.index("A3")
    if risk == "R2":
        return i >= LEVELS.index("A4")
    return risk == "R0"


def _constraint_problem(c: Mapping[str, Any], ctx: Mapping[str, Any]) -> str:
    if "max_risk_class" in c and not _risk_at_least(str(c["max_risk_class"]), str(ctx["risk_class"])):
        return f"risk {ctx['risk_class']} above the grant's {c['max_risk_class']}"
    if "cells" in c and ctx.get("cell") not in c["cells"]:
        return f"cell {ctx.get('cell')} not covered"
    if "max_per_day" in c and int(ctx.get("count_today", 0)) >= int(c["max_per_day"]):
        return f"already {ctx.get('count_today')} today (max {c['max_per_day']})"
    if "max_per_hour" in c and int(ctx.get("count_hour", 0)) >= int(c["max_per_hour"]):
        return f"already {ctx.get('count_hour')} this hour (max {c['max_per_hour']})"
    if "value_range" in c:
        value = dict(ctx.get("params", {})).get("value")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return "params.value is required by value_range"
        if not c["value_range"]["min"] <= value <= c["value_range"]["max"]:
            return f"value {value} outside {c['value_range']['min']}-{c['value_range']['max']}"
    if "power_stage_min" in c:
        stage, floor = ctx.get("power_stage"), c["power_stage_min"]
        if (
            stage not in POWER_STAGES
            or floor not in POWER_STAGES
            or POWER_STAGES.index(stage) < POWER_STAGES.index(floor)
        ):
            return f"power stage {stage} below {floor}"
    return ""


def decision_request(
    proposal: Mapping[str, Any],
    *,
    cell: str,
    risk_class: str,
    trust: str = "trusted",
    evaluation: str | None = None,
    now: float | None = None,
    **context: Any,
) -> dict[str, Any]:
    """The DecisionRequest for a proposal (spec decision-request)."""
    action = proposal["action"]
    ctx: dict[str, Any] = {
        "risk_class": risk_class,
        "trust": trust,
        "cell": cell,
        "now": to_rfc3339(time.time() if now is None else now),
        "params": dict(action.get("params", {})),
    }
    if evaluation is not None:
        ctx["evaluation"] = evaluation
    ctx.update(context)
    return {
        "proposal_id": proposal["proposal_id"],
        "principal": proposal["proposer"],
        "action": action["type"],
        "resource": action["target"],
        "context": ctx,
    }
