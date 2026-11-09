#!/usr/bin/env python3
"""Generate spec fixtures, test vectors and example component samples.

Everything is deterministic: test keys are derived from their names and
Ed25519 signatures are deterministic, so re-running produces identical files.

    python spec/tools/gen_fixtures.py          # rewrite
    python spec/tools/gen_fixtures.py --check  # fail if committed files are stale

The keys in fixtures/keys/test-keys.json are PUBLIC TEST KEYS. Never trust them
outside tests.
"""

from __future__ import annotations

import argparse
import copy
import filecmp
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

SPEC = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SPEC))

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat  # noqa: E402

from autonomy_conformance import crypto  # noqa: E402
from autonomy_conformance.jcs import canonicalize  # noqa: E402
from autonomy_conformance.schemas import BASE_URI  # noqa: E402

SIGNERS = [
    "owner:root", "owner:ops",
    "cell:field-station", "cell:home-hub", "agent:field-station", "adapter:ha",
    "svc:evaluator", "svc:pdp", "svc:action-gateway", "svc:verifier", "svc:job-service", "svc:approval",
]
CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
T0 = "2026-11-06T18:20:11Z"
TRACE = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
CORR = "corr_01JC9Q2F7T3B6W8K0M4N5P6Q7R"


def seed(name: str) -> bytes:
    return hashlib.sha256(b"autonomy-test-key:" + name.encode()).digest()


KEYS = {n: Ed25519PrivateKey.from_private_bytes(seed(n)) for n in SIGNERS}


def pub(name: str) -> str:
    raw = KEYS[name].public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return "ed25519:" + crypto.b64url_encode(raw)


def sign(obj: dict, kind: str) -> dict:
    sig_field, signer_field = crypto.SIGNED[kind]
    if kind == "audit-record":
        obj["hash"] = crypto.audit_hash(obj)
    signer = obj[signer_field].split("@", 1)[0]
    obj[sig_field] = "ed25519:" + crypto.b64url_encode(KEYS[signer].sign(crypto.signing_input(obj, sig_field)))
    return obj


def ulid(n: int) -> str:
    tail = ""
    for _ in range(4):
        tail = CROCKFORD[n % 32] + tail
        n //= 32
    return "01JC9Q3W8M2X7ZK4T5R6Y8" + tail


def envelope(source: str, type_: str, seq: int, data, *, schema: str | None = None, n: int | None = None,
             trust: str = "trusted", privacy: str = "site", subject: str | None = None,
             expires: str | None = None, causation: str | None = None, principal: str | None = None) -> dict:
    e = {"specversion": "1.0", "id": ulid(n if n is not None else seq), "source": source, "type": type_}
    if subject:
        e["subject"] = subject
    e["time"] = T0
    e["datacontenttype"] = "application/json"
    if schema:
        e["dataschema"] = BASE_URI + f"{schema}.schema.json"
    e["data"] = data
    e["seq"] = seq
    if expires:
        e["expires"] = expires
    e["correlationid"] = CORR
    if causation:
        e["causationid"] = causation
    e.update({"traceparent": TRACE, "principal": principal or source, "trust": trust, "privacy": privacy})
    return sign(e, "envelope")


# --- the action story from design/component-contracts.md §8 -----------------

HELPER = "input_number.farm_irrigation_recommended_climate_factor"


def proposal() -> dict:
    return {
        "proposal_id": "prp_01JC9Q40000000000000000001",
        "proposer": "agent:field-station",
        "objective": {"id": "obj_irrigation_tuning", "text": "Keep tree row soil within learned band while minimising water"},
        "action": {"type": "ha.helper.set", "target": HELPER, "params": {"value": 118}},
        "rationale": "ET0 3-day mean 6.1 mm vs baseline 5.2 mm; tree row drying rate above band for 2 days.",
        "inputs": [
            {"ref": f"evt:{ulid(1)}", "trust": "trusted", "observed_at": "2026-11-06T18:20:09Z"},
            {"ref": "fact:et0_daily_2026-11-06", "trust": "trusted"},
        ],
        "expected_effect": {"kind": "state_equals", "entity": HELPER, "value": 118, "within_s": 30},
        "verification": {"probe": "ha.state", "independent": False, "note": "Helper write; downstream effect verified by A01 next day via soil wet-up"},
        "reversibility": {"undo": {"type": "ha.helper.set", "params": {"value": "previous"}}},
        "freshness_s": 3600,
        "idempotency_key": "field-station:climate_factor:2026-11-07",
    }


def evaluation() -> dict:
    return {
        "evaluation_id": "evl_01JC9Q40000000000000000001", "proposal_id": proposal()["proposal_id"], "risk_class": "R1",
        "checks": [
            {"name": "schema", "ok": True}, {"name": "inputs_fresh", "ok": True},
            {"name": "invariant.value_range", "ok": True}, {"name": "conflicts", "ok": True},
            {"name": "llm_review", "ok": True, "advisory": True, "model": "local:qwen2.5-14b", "note": "rationale consistent with inputs"},
        ],
        "outcome": "ok", "escalation_reasons": [], "evaluated_at": "2026-11-06T18:24:58Z",
    }


def evaluation_escalate() -> dict:
    e = evaluation()
    e["evaluation_id"] = "evl_01JC9Q40000000000000000002"
    e["checks"][4] = {"name": "llm_review", "ok": False, "advisory": True, "model": "local:qwen2.5-14b", "note": "rationale cites a sensor that reported invalid"}
    e["outcome"] = "escalate"
    e["escalation_reasons"] = ["advisory review disagreed with rationale"]
    return e


def decision_request() -> dict:
    return {
        "proposal_id": proposal()["proposal_id"], "principal": "agent:field-station", "action": "ha.helper.set", "resource": HELPER,
        "context": {"risk_class": "R1", "evaluation": "ok", "trust": "trusted", "cell": "field-station", "power_stage": "normal",
                    "count_today": 1, "now": "2026-11-06T18:25:00Z", "params": {"value": 118}},
    }


def decision(effect: str = "permit") -> dict:
    p = proposal()
    d = {
        "decision_id": "dec_01JC9Q40000000000000000001" if effect == "permit" else "dec_01JC9Q40000000000000000002",
        "proposal_id": p["proposal_id"], "effect": effect,
        "matched": ["grt_2026_11_farm_helpers", "policy:farm/helpers.cedar#p3"],
        "obligations": [{"type": "verify"}, {"type": "rate_limit", "key": "agent:field-station:ha.helper.set", "max_per_day": 4}],
        "params_hash": crypto.params_hash(p["action"]),
        "decided_at": "2026-11-06T18:25:00Z", "expires": "2026-11-06T18:40:00Z",
        "policy_bundle": "2026.11.06-1", "issuer": "svc:pdp@field-station",
    }
    if effect == "require_approval":
        d["obligations"].append({"type": "approval", "approver": "owner:ops"})
        d["expires"] = "2026-11-06T19:25:00Z"
    if effect == "deny":
        d["obligations"] = []
        d["reason"] = "no grant covers this resource"
    return sign(d, "decision")


def approval() -> dict:
    d = decision("require_approval")
    return sign({
        "approval_id": "apr_01JC9Q40000000000000000001", "decision_id": d["decision_id"], "params_hash": d["params_hash"],
        "approver": "owner:ops", "method": "telegram_totp", "approved_at": "2026-11-06T18:31:02Z",
        "expires": "2026-11-06T18:46:02Z", "comment": "ok for this week",
    }, "approval")


def action_request(with_approval: bool = False) -> dict:
    r = {"action_id": "act_01JC9Q40000000000000000001", "proposal": proposal(),
         "decision": decision("require_approval" if with_approval else "permit"),
         "idempotency_key": proposal()["idempotency_key"]}
    if with_approval:
        r["action_id"] = "act_01JC9Q40000000000000000002"
        r["approval"] = approval()
    return r


def execution_record(status: str = "succeeded") -> dict:
    x = {"action_id": "act_01JC9Q40000000000000000001", "status": status, "adapter": "adapter:ha@field-station",
         "started_at": "2026-11-06T18:25:01Z", "finished_at": "2026-11-06T18:25:02Z", "adapter_response": {"ok": True},
         "before": {"value": 104}, "after_reported": {"value": 118}, "verification_pending": True,
         "executor": "svc:action-gateway@field-station"}
    if status == "rejected":
        x = {"action_id": "act_01JC9Q40000000000000000003", "status": "rejected",
             "error": {"code": "validation.expired", "message": "decision dec_01JC9Q40000000000000000001 expired at 2026-11-06T18:40:00Z", "retryable": False},
             "executor": "svc:action-gateway@field-station"}
    return sign(x, "execution-record")


def verification() -> dict:
    return sign({
        "verification_id": "ver_01JC9Q40000000000000000001", "action_id": "act_01JC9Q40000000000000000001", "result": "confirmed",
        "observations": [{"ref": f"evt:{ulid(9)}", "value": 118, "observed_at": "2026-11-06T18:25:09Z", "path": "ha.state"}],
        "independent": False, "verified_at": "2026-11-06T18:25:10Z", "verifier": "svc:verifier@field-station",
    }, "verification")


def audit_chain() -> list[dict]:
    rows = [
        ("svc:pdp@field-station", "action.decided", {"proposal": proposal()["proposal_id"], "decision": "dec_01JC9Q40000000000000000001"}, "Permitted ha.helper.set on recommended climate factor (grant grt_2026_11_farm_helpers)", "2026-11-06T18:25:00Z"),
        ("svc:action-gateway@field-station", "action.succeeded", {"proposal": proposal()["proposal_id"], "decision": "dec_01JC9Q40000000000000000001", "action": "act_01JC9Q40000000000000000001"}, "Set recommended climate factor 104 → 118", "2026-11-06T18:25:02Z"),
        ("svc:verifier@field-station", "action.verified", {"action": "act_01JC9Q40000000000000000001", "verification": "ver_01JC9Q40000000000000000001"}, "Confirmed climate factor reads 118", "2026-11-06T18:25:10Z"),
    ]
    out, prev = [], crypto.GENESIS_HASH
    for i, (actor, kind, refs, summary, t) in enumerate(rows):
        rec = sign({"seq": i, "prev_hash": prev, "cell": "field-station", "time": t, "actor": actor, "kind": kind,
                    "correlationid": CORR, "refs": refs, "summary": summary}, "audit-record")
        out.append(rec)
        prev = rec["hash"]
    return out


def grant() -> dict:
    return sign({
        "grant_id": "grt_2026_11_farm_helpers", "grantee": "agent:field-station", "responsibility": "irrigation.recommendation",
        "autonomy_level": "A2", "actions": ["ha.helper.set"],
        "resources": {"entities": [HELPER, "input_text.farm_agent_daily_note"]},
        "constraints": {"max_per_day": 4, "value_range": {"min": 30, "max": 200}, "cells": ["field-station"], "power_stage_min": "normal", "max_risk_class": "R1"},
        "requires_verification": True, "valid_from": "2026-11-10T00:00:00Z", "expires": "2026-12-10T00:00:00Z", "issued_by": "owner:ops",
    }, "grant")


def manifest_agent() -> dict:
    return {
        "$schema": BASE_URI + "manifest.schema.json",
        "component_id": "agent:field-station", "version": "0.5.0", "kind": "agent",
        "description": "Observes the Field Station and proposes irrigation adjustments.",
        "repository": "https://github.com/realsystem/farm_agent",
        "contracts": {"envelope": "^0.1", "health": "^0.1", "action-lifecycle": "^0.1", "audit": "^0.1"},
        "capabilities": {
            "provides": [{"name": "farm.assessment.daily", "version": "1.0", "type": "event", "schema": "schemas/assessment.json"}],
            "requires": [
                {"name": "ha.state.read", "version": "^1", "scope": {"entities": ["sensor.farm_*", "binary_sensor.farm_*", "sensor.smartshunt_*", "sensor.eco_worthy_*"]}},
                {"name": "model.chat", "version": "^1", "privacy": "cloud_ok"},
                {"name": "knowledge.space", "version": "^1", "scope": {"space": "agent:field-station"}},
            ],
            "proposes_actions": ["ha.helper.set", "notify.owner"],
        },
        "trust_class": "trusted-input",
        "data": {"owns": [{"store": "sqlite:/data/agent.db", "kind": "agent_memory", "retention_days": 365}],
                 "reads_from": ["events:observation.ha.*@field-station"]},
        "runtime": {"packaging": ["ha-addon"], "health": {"liveness": "/v1/health", "readiness": "/v1/ready"}, "resources": {"memory_mb": 256, "cpu": 0.5}},
        "dependencies": ["svc:model-gateway", "adapter:ha@field-station", "cell:field-station"],
    }


def job(submission: bool = False) -> dict:
    j = {"job_id": "job_01JC9Q40000000000000000001", "job_type": "video.frames", "job_type_version": "1.2",
         "input": {"source": "https://www.youtube.com/watch?v=abc123", "fps": 1}, "input_trust": "untrusted", "privacy": "cloud_ok",
         "priority": 10, "max_attempts": 3, "timeout_s": 1800, "idempotency_key": "media:frames:yt:abc123:fps1",
         "submitted_by": "agent:research", "correlationid": CORR, "traceparent": TRACE,
         "requires": {"capabilities": ["gpu", "ollama"], "cell": None},
         "result_schema": BASE_URI + "jobs/video.frames.result.json", "status": "queued", "attempt": 0}
    if submission:
        for k in ("job_id", "status", "attempt"):
            del j[k]
    return j


CONFIG_HASH = "sha256:" + hashlib.sha256(b"example effective config").hexdigest()
REGISTRY_HASH = "sha256:" + hashlib.sha256(b"example registry").hexdigest()


def sign_with(obj: dict, field: str, signer: str) -> dict:
    obj.pop(field, None)
    obj[field] = "ed25519:" + crypto.b64url_encode(KEYS[signer].sign(canonicalize(obj)))
    return obj


def registry() -> dict:
    owners = sign_with({"root": pub("owner:root"), "ops": [{"key": pub("owner:ops"), "expires": "2027-11-08T00:00:00Z", "comment": "laptop"}]},
                       "signature", "owner:root")
    agent = manifest_agent()
    return {
        "registry_format": 1,
        "schema_version": 1,
        "fleet": {"name": "farm", "telegram": {"operators": [100000001]}, "alert_order": ["home-hub", "field-station"]},
        "sites": {"remote": {"timezone": "UTC"}, "home": None},
        "owners": owners,
        "nodes": {
            "field-station": {"site": "remote", "packaging": "ha-addon", "roles": ["ha"], "public_key": pub("cell:field-station"), "tailscale": "field-station"},
            "home-hub": {"site": "home", "packaging": "host", "roles": ["nvr", "archive"], "public_key": pub("cell:home-hub"), "tailscale": "home-hub"},
        },
        "components": {
            "agent:field-station": {"public_key": pub("agent:field-station"), "version": agent["version"],
                                    "manifest_hash": crypto.sha256_tagged(canonicalize(agent)), "cells": ["field-station"],
                                    "contracts": agent["contracts"]},
            "svc:job-service": {"public_key": pub("svc:job-service"), "version": "2.0.0",
                                "manifest_hash": "sha256:" + hashlib.sha256(b"job-service manifest").hexdigest(), "cells": ["home-hub"]},
        },
        "revoked": [{"id": "adapter:old-ha", "key": "ed25519:" + crypto.b64url_encode(hashlib.sha256(b"old key").digest()),
                     "revoked_at": "2026-11-01T00:00:00Z", "reason": "replaced by adapter:ha"}],
    }


def registry_signature(reg: dict | None = None, serial: int = 1) -> dict:
    reg = registry() if reg is None else reg
    return sign_with({"registry_hash": crypto.sha256_tagged(canonicalize(reg)), "serial": serial, "signed_at": "2026-11-08T12:00:00Z",
                      "signer": "owner:ops", "key": pub("owner:ops"), "comment": "add job-service"}, "signature", "owner:ops")


def policy_bundle() -> dict:
    return sign_with({
        "bundle": "2026.11.09-1", "issued_at": "2026-11-09T12:00:00Z",
        "policies": [
            {"id": "farm-helpers", "description": "R1 helper writes for the Field Station agent, within its grant",
             "cedar": 'permit (principal == Agent::"field-station", action == Action::"ha.helper.set", resource in Group::"farm_helpers")\n'
                      'when { context.risk_class == "R1" && context.evaluation == "ok" && context.trust == "trusted" && context has grant };'},
            {"id": "untrusted-needs-owner", "approvable": True, "description": "untrusted-derived changes need an owner",
             "cedar": 'permit (principal, action, resource)\nwhen { context.trust == "untrusted" && context.risk_class != "R0" && context has approval && context.approval.valid };'},
            {"id": "no-r3", "cedar": 'forbid (principal, action, resource) when { context.risk_class == "R3" };'},
        ],
        "groups": {"farm_helpers": [HELPER, "input_text.farm_agent_daily_note"]},
        "grants": [grant()],
        "signer": "owner:ops", "key": pub("owner:ops"), "comment": "first bundle",
    }, "signature", "owner:ops")


def attested_approval() -> dict:
    d = decision("require_approval")
    return sign_with({
        "approval_id": "apr_01JC9Q40000000000000000002", "decision_id": d["decision_id"], "params_hash": d["params_hash"],
        "approver": "owner:ops", "attested_by": "svc:approval@field-station", "method": "telegram_totp",
        "approved_at": "2026-11-06T18:31:02Z", "expires": "2026-11-06T18:46:02Z",
    }, "signature", "svc:approval")


def control_order(kind: str = "hold", scope: str = "all") -> dict:
    return sign_with({
        "order_id": f"ord_01JC9Q4000000000000000{kind.upper()[:4]}", "kind": kind, "scope": scope,
        "reason": "owner on holiday; nothing automatic", "issued_by": "owner:ops",
        "attested_by": "svc:approval@field-station", "issued_at": "2026-11-09T19:00:00Z",
    }, "signature", "svc:approval")


def valid_fixtures() -> dict[str, dict[str, object]]:
    p = proposal()
    obs = envelope("adapter:ha@field-station", "observation.ha.state_changed", 48213,
                   {"value": 18.4, "unit": "%", "valid": True, "observed_at": "2026-11-06T18:20:09Z"},
                   n=1, subject="sensor.farm_soil_tree_row_1_moisture")
    return {
        "envelope": {
            "observation": obs,
            "action-proposed": envelope("agent:field-station", "action.proposed", 731, p, schema="action-proposal", n=2,
                                        expires="2026-11-06T19:20:11Z", causation=ulid(1)),
            "health-reported": envelope("agent:field-station", "health.reported", 732,
                                        {"status": "degraded", "component_id": "agent:field-station", "version": "0.5.0", "uptime_s": 86400,
                                         "config_hash": CONFIG_HASH,
                                         "checks": [{"name": "model-gateway", "ok": False, "impact": "recommendations paused", "safety": False}]},
                                        schema="health", n=3),
        },
        "manifest": {"agent-field-station": manifest_agent()},
        "health": {"ok": {"status": "ok", "component_id": "svc:job-service", "version": "1.4.0", "uptime_s": 86400}},
        "ready": {"degraded": {"status": "degraded", "checks": [{"name": "sqlite", "ok": True}, {"name": "model-gateway", "ok": False, "impact": "recommendations paused"}], "config_hash": CONFIG_HASH}},
        "cell-status": {"field-station": {"cell": "field-station", "registry_hash": REGISTRY_HASH, "policy_bundle": "2026.11.06-1", "power_stage": "normal", "suspended": False,
                                          "peers": [{"cell": "home-hub", "reachable": True, "last_seen_at": "2026-11-06T18:19:40Z", "registry_hash": REGISTRY_HASH}]}},
        "error": {"policy-denied": {"error": {"code": "policy.requires_approval", "message": "Action ha.service.call on switch.pump requires approval", "retryable": False,
                                              "details": {"decision_id": "dec_01JC9Q40000000000000000002", "obligations": ["approval:owner"]}, "correlationid": CORR}}},
        "log-record": {"info": {"ts": T0, "level": "info", "component_id": "svc:action-gateway", "instance_id": "svc:action-gateway@field-station",
                                "msg": "action succeeded", "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736", "span_id": "00f067aa0ba902b7", "correlationid": CORR,
                                "action_id": "act_01JC9Q40000000000000000001"}},
        "grant": {"farm-helpers": grant()},
        "action-proposal": {"climate-factor": p},
        "evaluation": {"ok": evaluation(), "escalate": evaluation_escalate()},
        "decision-request": {"climate-factor": decision_request()},
        "decision": {"permit": decision("permit"), "require-approval": decision("require_approval"), "deny": decision("deny")},
        "approval": {"telegram": approval(), "attested": attested_approval()},
        "control-order": {"hold-all": control_order(), "suspend-agent": control_order("suspend", "agent:field-station"),
                          "resume": control_order("resume", "all")},
        "action-request": {"permit": action_request(), "approved": action_request(with_approval=True)},
        "execution-record": {"succeeded": execution_record(), "rejected": execution_record("rejected")},
        "verification": {"confirmed": verification()},
        "audit-record": {"chain": audit_chain()},
        "job": {"record": job(), "submission": job(submission=True)},
        "registry": {"two-site": registry()},
        "registry-signature": {"ops": registry_signature()},
        "policy-bundle": {"farm": policy_bundle()},
    }


def invalid_fixtures() -> list[tuple[str, str, str, str, object]]:
    """(kind, name, description, expected error substring, instance)."""
    out = []

    def case(kind, name, desc, expect, base, mutate, resign: str | None = None):
        obj = copy.deepcopy(base)
        mutate(obj)
        if resign:
            sign(obj, resign)
        out.append((kind, name, desc, expect, obj))

    def setp(path, value):
        def f(o):
            *head, last = path
            for k in head:
                o = o[k]
            if value is DELETE:
                del o[last]
            else:
                o[last] = value
        return f

    v = valid_fixtures()
    env_obs = v["envelope"]["observation"]
    env_prop = v["envelope"]["action-proposed"]
    E = "envelope"
    case(E, "missing-signature", "every envelope is signed", "'autonomysig' is a required property", env_obs, setp(["autonomysig"], DELETE))
    case(E, "wrong-specversion", "CloudEvents 1.0 only", "'1.0' was expected", env_obs, setp(["specversion"], "0.3"), E)
    case(E, "data-base64", "binary data is not used in v0", "data_base64", env_obs, setp(["data_base64"], "AAEC"), E)
    case(E, "tampered-data", "signature covers data", "does not verify", env_obs, setp(["data", "value"], 99.0))
    case(E, "uppercase-source", "component names are lowercase", "source", env_obs, setp(["source"], "adapter:HA@field-station"), None)
    case(E, "expires-before-time", "expires must be after time", "expires is not after time", env_prop, setp(["expires"], "2026-11-06T18:00:00Z"), E)
    case(E, "untrusted-input-trusted-envelope", "taint propagation", "must be trust: untrusted", env_prop, setp(["data", "inputs", 0, "trust"], "untrusted"), E)
    case(E, "proposal-missing-rationale", "data validates against its dataschema", "'rationale' is a required property", env_prop, setp(["data", "rationale"], DELETE), E)
    case(E, "wrong-dataschema", "type and dataschema must agree", "expected action-proposal", env_prop, setp(["dataschema"], BASE_URI + "job.schema.json"), E)
    case(E, "bad-ulid", "id is a ULID", "id", env_obs, setp(["id"], "evt-123"), E)

    M = "manifest"
    m = v["manifest"]["agent-field-station"]
    case(M, "reads-other-store", "only events: and api: references", "reads_from", m, setp(["data", "reads_from"], ["sqlite:/data/job-service.db"]))
    case(M, "kind-mismatch", "kind matches the component_id prefix", "component_id", m, setp(["kind"], "svc"))
    case(M, "contract-not-satisfiable", "contract ranges must admit this spec", "does not admit spec", m, setp(["contracts", "envelope"], "^1"))
    case(M, "actions-without-lifecycle", "proposing actions needs the action-lifecycle contract", "requires the action-lifecycle contract", m, setp(["contracts", "action-lifecycle"], DELETE))
    case(M, "self-dependency", "no self dependency", "depends on itself", m, setp(["dependencies"], ["agent:field-station@field-station"]))
    case(M, "unknown-field", "manifests are closed to catch typos", "Additional properties", m, setp(["reads_form"], []))
    case(M, "instance-as-component-id", "manifest ids carry no @cell", "component_id", m, setp(["component_id"], "agent:field-station@field-station"))

    G = "grant"
    g = v["grant"]["farm-helpers"]
    case(G, "r3-grant", "R3 is never grantable", "max_risk_class", g, setp(["constraints", "max_risk_class"], "R3"), G)
    case(G, "unknown-constraint", "unknown constraints fail closed", "max_volume_l", g, setp(["constraints", "max_volume_l"], 500), G)
    case(G, "expires-before-valid-from", "validity window", "expires is not after valid_from", g, setp(["expires"], "2026-11-01T00:00:00Z"), G)
    case(G, "issued-by-agent", "only the owner issues grants", "issued_by", g, setp(["issued_by"], "agent:field-station"))
    case(G, "tampered-range", "signature covers constraints", "does not verify", g, setp(["constraints", "value_range", "max"], 400))
    case(G, "inverted-range", "value_range min <= max", "min is greater than max", g, setp(["constraints", "value_range"], {"min": 200, "max": 30}), G)

    P = "action-proposal"
    p = v["action-proposal"]["climate-factor"]
    case(P, "undo-and-irreversible", "either undo or irreversible", "reversibility", p, setp(["reversibility", "irreversible"], True))
    case(P, "no-reversibility", "reversibility must be stated", "reversibility", p, setp(["reversibility"], {}))
    case(P, "state-equals-without-window", "state_equals needs within_s", "'within_s' is a required property", p, setp(["expected_effect", "within_s"], DELETE))
    case(P, "unknown-field", "proposals are closed", "Additional properties", p, setp(["priority"], 5))

    V = "evaluation"
    e = v["evaluation"]["ok"]
    case(V, "ok-with-failed-advisory", "advisory checks can only escalate (ADR-008)", "True was expected", e, setp(["checks", 4, "ok"], False))
    case(V, "escalate-without-reasons", "escalations say why", "escalation_reasons", e, setp(["outcome"], "escalate"))
    case(V, "reject-all-ok", "reject needs a failing check", "checks", e, setp(["outcome"], "reject"))

    D = "decision"
    d = v["decision"]["permit"]
    case(D, "approval-without-obligation", "require_approval carries an approval obligation", "obligations", d, setp(["effect"], "require_approval"), D)
    case(D, "unknown-obligation", "executors must understand every obligation", "'budget' is not one of", d, setp(["obligations", 0, "type"], "budget"), D)
    case(D, "rate-limit-without-key", "rate limits need a key", "'key' is a required property", d, setp(["obligations", 1, "key"], DELETE), D)
    case(D, "expires-before-decided", "validity window", "expires is not after decided_at", d, setp(["expires"], "2026-11-06T18:00:00Z"), D)

    A = "approval"
    a = v["approval"]["telegram"]
    case(A, "bad-params-hash", "sha256:<hex>", "params_hash", a, setp(["params_hash"], "abc"), A)
    case(A, "agent-approver", "only the owner approves", "approver", a, setp(["approver"], "agent:field-station"))

    R = "action-request"
    r = v["action-request"]["permit"]
    ra = v["action-request"]["approved"]

    def other_params(o):
        o["decision"]["params_hash"] = crypto.params_hash({**o["proposal"]["action"], "params": {"value": 150}})
        sign(o["decision"], "decision")
    case(R, "params-hash-mismatch", "decision binds the exact parameters", "does not bind proposal.action", r, other_params)
    case(R, "missing-approval", "require_approval needs an approval", "'approval' is a required property", ra, setp(["approval"], DELETE))
    case(R, "deny-decision", "deny decisions cannot be executed", "'deny' is not one of", r, setp(["decision"], decision("deny")))

    def other_decision(o):
        o["approval"]["decision_id"] = "dec_01JC9Q4000000000000000OTHER"
        sign(o["approval"], "approval")
    case(R, "approval-for-other-decision", "approval binds to one decision", "approval is for a different decision", ra, other_decision)
    case(R, "idempotency-key-differs", "one idempotency key per story", "differs from proposal.idempotency_key", r, setp(["idempotency_key"], "other"))
    case(R, "tampered-decision", "nested decision signature is checked", "does not verify", r, setp(["decision", "obligations"], []))

    X = "execution-record"
    x = v["execution-record"]["succeeded"]
    case(X, "succeeded-without-start", "succeeded needs started_at", "'started_at' is a required property", x, setp(["started_at"], DELETE), X)
    case(X, "failed-without-error", "failures carry an error", "'error' is a required property", x, setp(["status"], "failed"), X)
    case(X, "finished-before-started", "time order", "finished_at is before started_at", x, setp(["finished_at"], "2026-11-06T18:24:00Z"), X)

    case("verification", "confirmed-without-evidence", "confirmed needs observations", "should be non-empty",
         v["verification"]["confirmed"], setp(["observations"], []), "verification")

    chain = v["audit-record"]["chain"]
    case("audit-record", "hash-mismatch", "hash covers the record", "hash does not match", chain[1], setp(["summary"], "Set recommended climate factor 104 → 200"), None)
    out[-1] = (*out[-1][:4], _resign_keep_hash(out[-1][4]))
    case("audit-record", "genesis-prev-hash", "seq 0 starts from the genesis hash", "genesis prev_hash", chain[1], setp(["seq"], 0), "audit-record")

    case("health", "failed-check-without-impact", "impact is mandatory on failed checks", "'impact' is a required property",
         v["ready"]["degraded"] | {"component_id": "svc:job-service", "version": "1.4.0"}, setp(["checks", 1, "impact"], DELETE))
    case("ready", "missing-config-hash", "readiness exposes the effective config hash", "'config_hash' is a required property", v["ready"]["degraded"], setp(["config_hash"], DELETE))
    case("error", "retryable-validation", "validation errors are not retryable", "False was expected", v["error"]["policy-denied"], setp(["error", "retryable"], True))
    case("error", "unknown-family", "known code families only", "code", v["error"]["policy-denied"], setp(["error", "code"], "oops.failed"))
    case("job", "missing-idempotency-key", "exactly-once submission", "'idempotency_key' is a required property", job(True), setp(["idempotency_key"], DELETE))
    case("job", "bad-traceparent", "W3C traceparent", "traceparent", job(True), setp(["traceparent"], "abc"))
    case("log-record", "unknown-level", "four levels", "'trace' is not one of", v["log-record"]["info"], setp(["level"], "trace"))
    case("cell-status", "missing-registry-hash", "cells report which registry they enforce", "'registry_hash' is a required property", v["cell-status"]["field-station"], setp(["registry_hash"], DELETE))
    reg = v["registry"]["two-site"]
    case("registry", "ops-added-without-root", "only root changes owners", "root signature does not verify", reg,
         lambda o: o["owners"]["ops"].append({"key": pub("adapter:ha"), "expires": "2027-01-01T00:00:00Z"}))
    case("registry", "shared-key", "one key, one identity", "key already used by", reg,
         setp(["components", "svc:job-service", "public_key"], pub("cell:home-hub")))
    case("registry", "unknown-cell", "components run on listed cells", "is not a node of this registry", reg,
         setp(["components", "svc:job-service", "cells"], ["gpu-pc"]))
    case("registry", "revoked-still-listed", "revoked identities stay out", "is revoked but still listed", reg,
         setp(["revoked", 0, "id"], "svc:job-service"))
    case("registry", "unknown-section", "closed at the top level", "Additional properties", reg, setp(["grants"], {}))
    case("registry", "node-without-key", "every cell has a key", "'public_key' is a required property", reg,
         setp(["nodes", "home-hub", "public_key"], DELETE))
    case("registry", "unknown-site", "nodes are on listed sites", "is not in sites", reg, setp(["nodes", "home-hub", "site"], "moon"))
    case("registry", "double-hyphen-name", "cell names use single hyphens", "home--hub", reg,
         setp(["nodes", "home--hub"], {"site": "home", "public_key": pub("svc:verifier")}))
    rs = v["registry-signature"]["ops"]
    case("registry-signature", "tampered-serial", "serial is signed", "signature does not verify", rs, setp(["serial"], 99))
    case("registry-signature", "serial-zero", "serials start at 1", "less than the minimum", rs, setp(["serial"], 0))
    case("registry-signature", "agent-signer", "only owners sign registries", "signer", rs, setp(["signer"], "agent:field-station"))
    pb = v["policy-bundle"]["farm"]
    case("policy-bundle", "tampered-policy", "policies are signed", "signature does not verify", pb, setp(["policies", 2, "cedar"], "forbid (principal, action, resource) when { false };"))
    case("policy-bundle", "two-statements", "one statement per policy", "exactly one permit or forbid", pb,
         setp(["policies", 2, "cedar"], 'forbid (principal, action, resource) when { false };\npermit (principal, action, resource);'), None)
    case("policy-bundle", "bad-version", "YYYY.MM.DD-n", "bundle", pb, setp(["bundle"], "v2"))
    case("policy-bundle", "duplicate-policy", "policy ids are unique", "used twice", pb, lambda o: o["policies"].append(dict(o["policies"][0])))
    case("approval", "attested-by-other-key", "attestation verifies with the attester's key", "does not verify",
         v["approval"]["attested"], setp(["attested_by"], "svc:verifier@field-station"))
    co = v["control-order"]["hold-all"]
    case("control-order", "unknown-kind", "suspend, hold or resume", "'pause' is not one of", co, setp(["kind"], "pause"))
    case("control-order", "bad-scope", "all, a cell or a component", "scope", co, setp(["scope"], "everything"))
    case("control-order", "tampered-scope", "orders are signed", "does not verify", co, setp(["scope"], "agent:field-station"))
    case("decision-request", "missing-now", "policy needs the time", "'now' is a required property", v["decision-request"]["climate-factor"], setp(["context", "now"], DELETE))
    return out


class _Delete:
    pass


DELETE = _Delete()


def _resign_keep_hash(rec: dict) -> dict:
    """Re-sign without recomputing hash, so only the hash check fails."""
    rec = copy.deepcopy(rec)
    rec["signature"] = "ed25519:" + crypto.b64url_encode(KEYS[rec["actor"].split("@")[0]].sign(crypto.signing_input(rec, "signature")))
    return rec


# --- vectors ---------------------------------------------------------------

JCS_VECTORS = {
    # U+FF61 sorts after U+1F600 in UTF-16 code units (0xFF61 > 0xD83D) but before it by code point.
    "key-order-utf16": {"\u20ac": "euro", "\r": "cr", "\U0001f600": "emoji", "\uff61": "halfwidth-stop", "1": "one", "\u00f6": "o-umlaut"},
    "numbers": [0, -0.0, 1, -1, 1.0, 4.50, 2e-3, 0.000001, 1e-7, 1e20, 1e21, 1e30, 333333333.33333329, 9007199254740991, -9007199254740991, 1.5e-300, 123456789012345680000.0],
    "strings": ["\u0000\u0008\u0009\u000a\u000c\u000d\u001f", "quote \" backslash \\ slash /", "\u007f del", "café   \U0001f600"],
    "nested": {"b": [True, False, None, {"z": 1, "a": {"y": [], "x": {}}}], "a": "x"},
}


def vectors() -> dict[str, object]:
    out = {}
    for name, value in JCS_VECTORS.items():
        out[f"jcs/{name}.json"] = {"input": value, "canonical": canonicalize(value).decode("utf-8")}
    p = proposal()
    out["hash/params-hash.json"] = {"action": p["action"], "canonical": canonicalize(p["action"]).decode(), "params_hash": crypto.params_hash(p["action"])}
    rec = audit_chain()[0]
    out["hash/audit-hash.json"] = {"record": rec, "hash_input": rec["prev_hash"] + canonicalize(crypto.without(rec, "hash", "signature")).decode(), "hash": rec["hash"]}
    e = valid_fixtures()["envelope"]["observation"]
    out["signature/envelope.json"] = {
        "signer": "adapter:ha", "private_seed_hex": seed("adapter:ha").hex(), "public_key": pub("adapter:ha"),
        "envelope": e, "signing_input": crypto.signing_input(e, "autonomysig").decode(), "autonomysig": e["autonomysig"],
    }
    return out


# --- example components (C07, C10) -----------------------------------------

def examples() -> dict[str, dict[str, object]]:
    js = {
        "$schema": BASE_URI + "manifest.schema.json",
        "component_id": "svc:job-service", "version": "2.0.0", "kind": "svc",
        "description": "Durable SQLite job queue with leases, retries and a dead-letter queue (processor, worker API v2).",
        "repository": "https://github.com/realsystem/processor",
        "contracts": {"envelope": "^0.1", "health": "^0.1", "error": "^0.1", "logging": "^0.1", "job": "^0.1"},
        "capabilities": {"provides": [{"name": "job.submit", "version": "2.0", "type": "api"}, {"name": "job.lifecycle", "version": "1.0", "type": "event"}]},
        "trust_class": "trusted-input",
        "data": {"owns": [{"store": "sqlite:/data/jobs.db", "kind": "job_queue", "retention_days": 90}]},
        "runtime": {"packaging": ["binary", "docker"], "health": {"liveness": "/v1/health", "readiness": "/v1/ready"}, "resources": {"memory_mb": 128, "cpu": 0.5}},
        "dependencies": [],
    }
    jq = job()
    js_samples = {
        "job-queued.json": envelope("svc:job-service", "job.queued", 1, jq, schema="job", n=101, trust="untrusted", principal="agent:research"),
        "job-succeeded.json": envelope("svc:job-service", "job.succeeded", 2, jq | {"status": "succeeded", "attempt": 1, "result": {"frames": 312}},
                                       schema="job", n=102, trust="untrusted", principal="agent:research"),
        "ready.ready.json": {"status": "ok", "component_id": "svc:job-service", "checks": [{"name": "sqlite", "ok": True}], "config_hash": CONFIG_HASH},
        "conflict.error.json": {"error": {"code": "conflict.idempotency_mismatch", "message": "idempotency key reused with a different input", "retryable": False, "correlationid": CORR}},
        "submission.job.json": job(submission=True),
    }
    cr = {
        "$schema": BASE_URI + "manifest.schema.json",
        "component_id": "cell:field-station", "version": "0.1.0", "kind": "cell",
        "description": "Cell runtime for the Field Station (edge_server, ha-addon packaging): identity, registry copy, outbox, heartbeats, power stages.",
        "repository": "https://github.com/realsystem/edge_server",
        "contracts": {"envelope": "^0.1", "health": "^0.1", "error": "^0.1", "logging": "^0.1"},
        "capabilities": {"provides": [{"name": "cell.status", "version": "1.0", "type": "api"}, {"name": "health.heartbeat", "version": "1.0", "type": "event"}]},
        "trust_class": "trusted-input",
        "data": {"owns": [{"store": "sqlite:/data/outbox.db", "kind": "outbox", "retention_days": 30}, {"store": "file:/data/registry", "kind": "registry_copy"}]},
        "runtime": {"packaging": ["ha-addon"], "health": {"liveness": "/v1/health", "readiness": "/v1/ready"}, "resources": {"memory_mb": 96, "cpu": 0.25}},
        "dependencies": [],
    }
    cr_samples = {
        "heartbeat.json": envelope("cell:field-station", "health.heartbeat", 5001, {"power_stage": "normal", "uptime_s": 3600}, n=201),
        "health-reported.json": envelope("cell:field-station", "health.reported", 5002,
                                         {"status": "ok", "component_id": "cell:field-station", "version": "0.1.0", "uptime_s": 3600, "config_hash": CONFIG_HASH, "checks": [{"name": "outbox", "ok": True}]},
                                         schema="health", n=202),
        "cell.cell-status.json": valid_fixtures()["cell-status"]["field-station"],
        "log.log-record.jsonl": [
            {"ts": T0, "level": "info", "component_id": "cell:field-station", "msg": "registry verified", "registry_hash": REGISTRY_HASH},
            {"ts": T0, "level": "warn", "component_id": "cell:field-station", "msg": "peer unreachable", "peer": "home-hub"},
        ],
    }
    return {"job-service": {"manifest": js, "samples": js_samples}, "cell-runtime": {"manifest": cr, "samples": cr_samples}}


# --- writing ---------------------------------------------------------------

def dump(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix == ".jsonl":
        path.write_text("".join(json.dumps(o, ensure_ascii=False) + "\n" for o in obj), encoding="utf-8")
    else:
        path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def generate(root: Path) -> None:
    fx = root / "fixtures"
    dump(fx / "keys" / "test-keys.json", {
        "_warning": "PUBLIC TEST KEYS derived from sha256('autonomy-test-key:' + name). Never trust them outside tests.",
        "public": {n: pub(n) for n in SIGNERS},
        "private_seed_hex": {n: seed(n).hex() for n in SIGNERS},
    })
    for kind, items in valid_fixtures().items():
        for name, obj in items.items():
            suffix = ".jsonl" if isinstance(obj, list) else ".json"
            dump(fx / "v0" / kind / "valid" / f"{name}{suffix}", obj)
    for kind, name, desc, expect, obj in invalid_fixtures():
        dump(fx / "v0" / kind / "invalid" / f"{name}.json", {"description": desc, "expect": expect, "instance": obj})
    for rel, obj in vectors().items():
        dump(root / "vectors" / rel, obj)
    for comp, parts in examples().items():
        dump(root / "examples" / comp / "autonomy.manifest.json", parts["manifest"])
        for rel, obj in parts["samples"].items():
            dump(root / "examples" / comp / "samples" / rel, obj)


GENERATED = ["fixtures", "vectors", "examples"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="fail if the committed files differ from a fresh generation")
    args = ap.parse_args()
    if args.check:
        with tempfile.TemporaryDirectory() as tmp:
            generate(Path(tmp))
            stale = []
            for d in GENERATED:
                stale += _diff(Path(tmp) / d, SPEC / d)
            for s in stale:
                print(f"stale: {s}")
            print("generated files are up to date" if not stale else "run: python spec/tools/gen_fixtures.py")
            return 1 if stale else 0
    for d in GENERATED:
        shutil.rmtree(SPEC / d, ignore_errors=True)
    generate(SPEC)
    print(f"wrote {', '.join(GENERATED)} under {SPEC}")
    return 0


def _diff(a: Path, b: Path) -> list[str]:
    cmp = filecmp.dircmp(a, b)
    out = [str(b / n) for n in cmp.left_only + cmp.right_only + cmp.diff_files]
    for sub in cmp.common_dirs:
        out += _diff(a / sub, b / sub)
    # dircmp compares shallowly by stat; recheck common files by content.
    for n in cmp.same_files:
        if (a / n).read_bytes() != (b / n).read_bytes():
            out.append(str(b / n))
    return out


if __name__ == "__main__":
    sys.exit(main())
