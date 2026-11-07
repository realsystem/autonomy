# Component Contracts

**Status:** Draft 0.1 (target: `autonomy/spec` v0.1) · 2026-11-06

This is the most important document in the package. A component joins Autonomy by implementing these contracts, not by being modified into a central program. Everything here is **[D]** unless tagged.

## 0. Conformance levels

| Level | Who | Must implement |
|---|---|---|
| **L0 — Participant** | Every component, agent and adapter | §1 Identity, §2 Manifest, §3 Envelope (sign + verify), §4 Health, §6 Logging/trace, §7 Errors, §11 Config conventions |
| **L1 — Actor** | Anything that decides, approves, executes or verifies | L0 + §8 Action lifecycle (its part) + §9 Audit |
| **L2 — Worker** | Job producers and executors | L0 + §10 Job |
| Optional | Where useful | §12 MCP tool exposure, §13 A2A boundary, §5 Events (publishing beyond health) |

A conformance kit in `autonomy/spec` provides JSON Schemas, fixtures and a CLI (`autonomy-conformance check --manifest m.json --samples samples/`) that every repository runs in CI.

## 1. Identity

| Item | Rule |
|---|---|
| `component_id` | `<kind>:<name>` where kind ∈ `cell`, `svc`, `agent`, `adapter`, `app`, `owner`; name = lowercase letters, digits, hyphens (same rule as `edge_server` node names [V]). Examples: `cell:field-station`, `svc:job-service`, `agent:field-station`, `adapter:ha@field-station` |
| Instance | `instance_id` = `<component_id>#<cell>` when the same component runs on several cells |
| Key | Ed25519 key pair generated where the component runs; private key never leaves the host. Public key in the registry as `ed25519:<base64url>` |
| Owner | `owner:root` (offline, signs registry roots and root-level grants) and `owner:ops` (online, signs day-to-day approvals and grants up to limits set by root) |
| Rotation | New key registered with `previous_key` signature; old key valid for overlap window (default 7 days) |

## 2. Component manifest (`autonomy.manifest.json`)

Lives at the repository root and is published with each release. The registry stores its hash.

```json
{
  "$schema": "https://autonomy.local/spec/v0/manifest.schema.json",
  "component_id": "agent:field-station",
  "version": "0.5.0",
  "kind": "agent",
  "description": "Observes the Field Station and proposes irrigation adjustments.",
  "repository": "https://github.com/realsystem/farm_agent",
  "contracts": {
    "envelope": "^0.1",
    "health": "^0.1",
    "action-lifecycle": "^0.1",
    "audit": "^0.1"
  },
  "capabilities": {
    "provides": [
      {"name": "farm.assessment.daily", "version": "1.0", "type": "event", "schema": "schemas/assessment.json"}
    ],
    "requires": [
      {"name": "ha.state.read", "version": "^1", "scope": {"entities": ["sensor.farm_*", "binary_sensor.farm_*", "sensor.smartshunt_*", "sensor.eco_worthy_*"]}},
      {"name": "model.chat", "version": "^1", "privacy": "cloud_ok"},
      {"name": "knowledge.space", "version": "^1", "scope": {"space": "agent:field-station"}}
    ],
    "proposes_actions": ["ha.helper.set", "notify.owner"]
  },
  "trust_class": "trusted-input",
  "data": {
    "owns": [{"store": "sqlite:/data/agent.db", "kind": "agent_memory", "retention_days": 365}],
    "reads_from": ["events:observation.ha.*@field-station"]
  },
  "runtime": {
    "packaging": ["ha-addon"],
    "health": {"liveness": "/v1/health", "readiness": "/v1/ready"},
    "resources": {"memory_mb": 256, "cpu": 0.5}
  },
  "dependencies": ["svc:model-gateway", "adapter:ha@field-station", "cell:field-station"]
}
```

Rules:
- `capabilities.requires` is **what the component can ask for**, not what it is allowed. Authority comes only from grants (§8.2).
- `data.owns` lists stores no other component may access directly. A manifest that lists another component's store in `reads_from` fails conformance; only `events:` and `api:` references are allowed.
- `dependencies` must not introduce cycles (checked when the registry is signed).

## 3. Envelope

Every message crossing a component boundary — events, commands, action requests, decisions — is a **CloudEvents 1.0** structured-mode JSON document [R] with Autonomy extension attributes, signed by the sender.

```json
{
  "specversion": "1.0",
  "id": "01JC9Q3W8M2X7ZK4T5R6Y8B0N1",
  "source": "adapter:ha@field-station",
  "type": "observation.ha.state_changed",
  "subject": "sensor.farm_soil_tree_row_1_moisture",
  "time": "2026-11-06T18:20:11Z",
  "datacontenttype": "application/json",
  "dataschema": "https://autonomy.local/spec/v0/observation.schema.json",
  "data": {"value": 18.4, "unit": "%", "valid": true, "observed_at": "2026-11-06T18:20:09Z"},

  "seq": 48213,
  "expires": "2026-11-06T19:20:11Z",
  "correlationid": "corr_01JC9Q2...",
  "causationid": "01JC9Q1...",
  "traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
  "principal": "adapter:ha@field-station",
  "trust": "trusted",
  "privacy": "site",
  "autonomysig": "ed25519:2c9f...base64url"
}
```

| Attribute | Required | Meaning |
|---|---|---|
| `id` | yes | ULID; unique per source |
| `source` | yes | Sender `component_id`/instance |
| `type` | yes | Reverse-dotted event or command type (§5) |
| `seq` | yes | Monotonic per sender, survives restarts (as in `edge_server` `Counter` [V]); used for dedup and ordering |
| `expires` | yes for commands, optional for events | Receiver rejects after this time |
| `correlationid` | yes | Ties everything belonging to one objective/incident/action story |
| `causationid` | when applicable | `id` of the envelope that caused this one |
| `traceparent` | yes | W3C Trace Context |
| `principal` | yes | Identity on whose behalf this is sent (usually = source; for delegated work, the delegating agent) |
| `trust` | yes | `trusted` (from a registered component about its own domain) · `owner` (owner-originated) · `untrusted` (derived from external content: web, email, third-party messages, transcripts) |
| `privacy` | yes | `public` · `site` · `private` · `secret-adjacent`; drives routing in C12 and sync filters in C09 |
| `autonomysig` | yes | Ed25519 signature over the RFC 8785 canonical JSON of all other attributes including `data` |

**Taint propagation.** If any input used to produce `data` was `untrusted`, the output's `trust` is `untrusted`. Components that cannot track this mark everything they emit after reading untrusted input as `untrusted` for the rest of that task (the conservative choice, following the dual-LLM/CaMeL reasoning [R]).

Receivers verify: signature against registry key, `source` registered and not revoked, `seq` not seen, `expires` not passed, `time` within skew (default ±5 min), schema valid.

## 4. Health and readiness

```http
GET /v1/health   →  200 {"status":"ok","component_id":"svc:job-service","version":"1.4.0","uptime_s":86400}
GET /v1/ready    →  200 | 503 {"status":"degraded","checks":[{"name":"sqlite","ok":true},{"name":"model-gateway","ok":false,"impact":"recommendations paused"}]}
GET /v1/cell     →  (C10 only) {"cell":"field-station","registry_hash":"sha256:…","policy_bundle":"2026.11.06-1","power_stage":"normal","peers":[…]}
```

`impact` is mandatory on failed checks so the owner can tell "degraded intelligence" from "degraded safety". Components that are not HTTP servers (CLIs, add-ons without ports) publish `health.reported` events every 60 s instead.

## 5. Event type conventions

`<domain>.<entity>.<verb_past_tense>` for facts, `<domain>.<entity>.<verb>` for commands.

| Prefix | Examples | Producer |
|---|---|---|
| `observation.*` | `observation.ha.state_changed`, `observation.power.reading` | C20 |
| `assessment.*` | `assessment.farm.daily` | Agents |
| `plan.*` | `plan.submitted`, `plan.revised`, `plan.abandoned` | Agents |
| `action.*` | `action.proposed`, `action.evaluated`, `action.decided`, `action.approved`, `action.started`, `action.succeeded`, `action.failed`, `action.verified` | C11, C13, C04, C05, C06, C14 |
| `job.*` | `job.queued` … `job.dead_lettered` | C07 |
| `authority.*` | `authority.granted`, `authority.revoked`, `system.suspended`, `system.resumed` | C05, C03 |
| `incident.*` | `incident.opened`, `incident.updated`, `incident.closed` | C10, A02 |
| `health.*` | `health.reported`, `heartbeat` | All |

Event schemas are versioned independently. Consumers must ignore unknown fields (forward compatibility).

## 6. Logging and tracing

- JSON lines to stdout/stderr: `ts`, `level`, `component_id`, `instance_id`, `msg`, `trace_id`, `span_id`, `correlationid`, plus structured fields. No secrets, no raw LLM prompts at `info` level.
- OpenTelemetry traces with W3C `traceparent` copied into and out of envelopes. Span names: `autonomy.<component>.<operation>`; LLM spans follow the OpenTelemetry GenAI conventions where stable [R] (they are still evolving and have moved to a separate repository, so we pin a version).
- Audit (§9) is separate from logs: logs may be dropped; audit records may not.

## 7. Error model

```json
{"error": {"code": "policy.denied", "message": "Action ha.service.call on switch.pump requires approval", "retryable": false,
           "details": {"decision_id": "dec_…", "obligations": ["approval:owner"]}, "correlationid": "corr_…"}}
```

| Code family | Retry? | Examples |
|---|---|---|
| `validation.*` | No | `validation.schema`, `validation.expired`, `validation.signature` |
| `policy.*` | No (until context changes) | `policy.denied`, `policy.requires_approval`, `policy.bundle_missing` |
| `conflict.*` | Maybe | `conflict.idempotency_mismatch`, `conflict.resource_locked` |
| `unavailable.*` | Yes, with backoff | `unavailable.adapter`, `unavailable.model`, `unavailable.peer` |
| `internal.*` | Yes, limited | `internal.error` |

## 8. Action lifecycle contracts

### 8.1 Risk classes (unify `edge_server` T0–T3 [V] and `jarvis` read/write/sensitive [V])

| Class | Meaning | Examples | Default rule |
|---|---|---|---|
| **R0 observe** | Read-only, no side effects outside Autonomy | read HA state, read logs | Permitted by grant, no evaluation required |
| **R1 reversible-local** | Reversible, bounded, low impact | write a recommendation helper, restart a service, send owner a notification | Permitted within grant + rate limit; evaluated |
| **R2 consequential** | Changes the physical world, external parties, money or data, reversibly but with real cost | change irrigation water level, run a zone, send email to a third party, reboot a node, delete data within retention | `require_approval` unless a grant at A3/A4 covers it with explicit bounds |
| **R3 never-remote** | Irreversible or could cut access/safety | change pump controller settings, firewall/Tailscale ACL changes, disk formatting, disable safety interlocks | Always deny remotely; on-site manual only |

### 8.2 Grant

```json
{
  "grant_id": "grt_2026_11_farm_helpers",
  "grantee": "agent:field-station",
  "responsibility": "irrigation.recommendation",
  "autonomy_level": "A2",
  "actions": ["ha.helper.set"],
  "resources": {"entities": ["input_number.farm_irrigation_recommended_climate_factor", "input_text.farm_agent_daily_note"]},
  "constraints": {"max_per_day": 4, "value_range": {"min": 30, "max": 200}, "cells": ["field-station"], "power_stage_min": "normal"},
  "requires_verification": true,
  "valid_from": "2026-11-10T00:00:00Z",
  "expires": "2026-12-10T00:00:00Z",
  "issued_by": "owner:ops",
  "signature": "ed25519:…"
}
```

### 8.3 ActionProposal (agent → C13)

```json
{
  "proposal_id": "prp_01JC…",
  "proposer": "agent:field-station",
  "objective": {"id": "obj_irrigation_tuning", "text": "Keep tree row soil within learned band while minimising water"},
  "action": {"type": "ha.helper.set", "target": "input_number.farm_irrigation_recommended_climate_factor", "params": {"value": 118}},
  "rationale": "ET0 3-day mean 6.1 mm vs baseline 5.2 mm; tree row drying rate above band for 2 days.",
  "inputs": [
    {"ref": "evt:01JC9Q3W…", "trust": "trusted", "observed_at": "2026-11-06T18:20:09Z"},
    {"ref": "fact:et0_daily_2026-11-06", "trust": "trusted"}
  ],
  "expected_effect": {"kind": "state_equals", "entity": "input_number.farm_irrigation_recommended_climate_factor", "value": 118, "within_s": 30},
  "verification": {"probe": "ha.state", "independent": false, "note": "Helper write; downstream effect verified by A01 next day via soil wet-up"},
  "reversibility": {"undo": {"type": "ha.helper.set", "params": {"value": "previous"}}},
  "freshness_s": 3600,
  "idempotency_key": "field-station:climate_factor:2026-11-07"
}
```

### 8.4 Evaluation (C13 → C04)

```json
{"evaluation_id": "evl_…", "proposal_id": "prp_…", "risk_class": "R1",
 "checks": [
   {"name": "schema", "ok": true}, {"name": "inputs_fresh", "ok": true},
   {"name": "invariant.value_range", "ok": true}, {"name": "conflicts", "ok": true},
   {"name": "llm_review", "ok": true, "advisory": true, "model": "local:qwen2.5-14b", "note": "rationale consistent with inputs"}
 ],
 "outcome": "ok", "escalation_reasons": []}
```

`outcome` ∈ `ok`, `escalate` (forces `require_approval`), `reject`.

### 8.5 DecisionRequest and Decision (C04)

```json
{"principal": "agent:field-station", "action": "ha.helper.set",
 "resource": "input_number.farm_irrigation_recommended_climate_factor",
 "context": {"risk_class": "R1", "evaluation": "ok", "trust": "trusted", "cell": "field-station", "power_stage": "normal",
             "count_today": 1, "now": "2026-11-06T18:25:00Z", "params": {"value": 118}}}
```

```json
{"decision_id": "dec_…", "proposal_id": "prp_…", "effect": "permit",
 "matched": ["grt_2026_11_farm_helpers", "policy:farm/helpers.cedar#p3"],
 "obligations": [{"type": "verify"}, {"type": "rate_limit", "key": "agent:field-station:ha.helper.set", "max_per_day": 4}],
 "params_hash": "sha256:…", "expires": "2026-11-06T18:40:00Z",
 "policy_bundle": "2026.11.06-1", "issuer": "svc:pdp@field-station", "signature": "ed25519:…"}
```

`effect` ∈ `permit`, `deny`, `require_approval`. `params_hash` binds the decision to the exact parameters.

### 8.6 Approval (C05)

```json
{"approval_id": "apr_…", "decision_id": "dec_…", "params_hash": "sha256:…", "approver": "owner:ops",
 "method": "telegram_totp", "approved_at": "2026-11-06T18:31:02Z", "expires": "2026-11-06T18:46:02Z",
 "comment": "ok for this week", "signature": "ed25519:…"}
```

### 8.7 ActionRequest (to C06) and ExecutionRecord

`ActionRequest` = `{action_id, proposal, decision, approval?, idempotency_key}`. C06 re-verifies everything and answers:

```json
{"action_id": "act_…", "status": "succeeded", "adapter": "adapter:ha@field-station",
 "started_at": "…", "finished_at": "…", "adapter_response": {"ok": true},
 "before": {"value": 104}, "after_reported": {"value": 118},
 "verification_pending": true, "executor": "svc:action-gateway@field-station", "signature": "ed25519:…"}
```

`status` ∈ `accepted`, `rejected`, `started`, `succeeded`, `failed`, `unknown`, `cancelled`. **`succeeded` means the adapter accepted the call; it does not mean the outcome happened.** Only C14's `Verification` says that.

### 8.8 Verification (C14)

```json
{"verification_id": "ver_…", "action_id": "act_…", "result": "confirmed",
 "observations": [{"ref": "evt:…", "value": 118, "observed_at": "…", "path": "ha.state"}],
 "independent": false, "verifier": "svc:verifier@field-station", "signature": "ed25519:…"}
```

`result` ∈ `confirmed`, `not_confirmed` (no evidence within window), `contradicted` (evidence of a different outcome), `inconclusive`.

## 9. Audit record

```json
{"seq": 10233, "prev_hash": "sha256:…", "hash": "sha256:…", "cell": "field-station",
 "time": "…", "actor": "svc:action-gateway@field-station", "kind": "action.succeeded",
 "correlationid": "corr_…", "refs": {"proposal": "prp_…", "decision": "dec_…", "approval": "apr_…", "action": "act_…"},
 "summary": "Set recommended climate factor 104 → 118", "signature": "ed25519:…"}
```

`hash = sha256(prev_hash || canonical(record without hash, signature))`. Chains are per cell; the archive cell stores copies and verifies continuity.

## 10. Job contract (C07, extends `processor` [V])

```json
{"job_id": "job_…", "job_type": "video.frames", "job_type_version": "1.2",
 "input": {"source": "https://…", "fps": 1}, "input_trust": "untrusted",
 "priority": 10, "max_attempts": 3, "timeout_s": 1800,
 "idempotency_key": "media:frames:yt:abc123:fps1",
 "submitted_by": "agent:research", "correlationid": "corr_…", "traceparent": "00-…",
 "requires": {"capabilities": ["gpu", "ollama"], "cell": null},
 "result_schema": "https://autonomy.local/spec/v0/jobs/video.frames.result.json"}
```

Executors register `job_type@version` with input/output schemas. Jobs carry `input_trust`; an executor that receives untrusted input may not submit `ActionRequest`s at R1+ with that job's `principal`.

## 11. Configuration conventions

- Layered: built-in defaults → `config/<component>.yaml` (in git, no secrets) → environment variables (`AUTONOMY_<COMPONENT>_<KEY>`) → command-line flags. Same precedence as `edge_server` fleet mode [V].
- Secrets only as references: `secret://<cell>/<name>` resolved by C18 at start.
- Every component exposes its effective config hash in `/v1/ready` and in audit records of its decisions.

## 12. Tool exposure via MCP (optional)

LLM-facing tools are published as MCP servers [R]. Rules:
- R0 tools may be served directly by the adapter's MCP server.
- R1+ tools are served **only** by C06's MCP facade; calling one creates an `ActionProposal`, not an execution. The tool result tells the model "proposed, pending decision" with the `proposal_id`.
- We target the MCP 2026-07-28 revision, which removed protocol sessions and the initialize handshake and moved long-running work to a Tasks extension [R]. Stateless servers fit our signed-envelope model; MCP's own OAuth is not used inside the federation (cells use registry keys).

## 13. A2A boundary (deferred)

A2A v1.0 (Linux Foundation) defines Agent Cards and a task lifecycle [R]. Autonomy does not use it internally. If an external agent must be integrated, an `adapter:a2a` will translate an A2A task into an `ActionProposal`/`Job` with `trust: untrusted`, and publish Autonomy capabilities as an Agent Card.

## 14. Versioning and compatibility

| Change | Version bump | Allowed in minor? |
|---|---|---|
| Add optional field | MINOR | Yes |
| Add event type | MINOR | Yes |
| Add enum value consumers must handle | MAJOR | No (unless the field is documented as open) |
| Remove/rename field, change meaning or type | MAJOR | No |
| Tighten validation | MAJOR | No |

- Each component supports the current and previous MAJOR of each contract it implements for at least 6 months.
- The registry refuses to sign a component whose `contracts` ranges have no overlap with the components it depends on.
- Breaking compatibility requires an ADR in `autonomy/spec`.

## 15. Joining Autonomy — the checklist

1. Generate a key; write `autonomy.manifest.json`.
2. Pass `autonomy-conformance` for your level in CI.
3. Submit an `EnrollmentRequest` (public key + manifest hash) to the owner; owner adds it to the registry and signs.
4. Owner issues grants for the capabilities you need.
5. Start; your health appears in the console; your events appear in C09; your actions go through C06.

No core component is modified.
