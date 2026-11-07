# Final Architectural Test — 14 Scenarios

**Status:** Draft 0.1 · 2026-11-06

Each scenario (ST-01…ST-14) names the components that handle it, whether the design supports it, and what changed in the design because of it. Scenarios become integration tests in the milestone named.

| # | Scenario | Handling | Supported? | Design change made | Test / milestone |
|---|---|---|---|---|---|
| ST-01 | **New agent developed independently and registered without modifying the core** | Agent writes manifest, passes conformance (C01), owner adds key + manifest hash to registry (C03), issues grants (C04/C05); C11 starts it; events via C09; actions via C06 | **Yes** | Registry refuses incompatible contract ranges (FR-006) | M3 |
| ST-02 | **New edge node joins** | Enrollment sequence ([distributed-runtime §2](distributed-runtime.md#2-node-identity-and-enrollment)): node generates key, owner verifies fingerprint out-of-band and signs registry; peers accept; node receives bundle | **Yes** | Out-of-band fingerprint check added to prevent rogue enrollment | M14 |
| ST-03 | **External AI provider unavailable** | C12 routing falls back (cloud → other cloud → local Ollama → rule-based); agents degrade; safety, schedules, policy and execution unaffected (no LLM in those paths); C13 runs deterministic checks only and marks LLM critique `skipped` | **Yes** | C13 must not require the LLM critique; `skipped` is not `ok`-blocking but recorded | M7 |
| ST-04 | **Edge node offline for several days** | Cell keeps local PDP, gateway, log, audit; R1 within valid grants continues; R2 approvals unreachable → expire; grants expiring offline drop agent to A1; outbox grows; sync with dedup on reconnect | **Yes**, with bounded revocation latency | Grant expiry limits per risk class (R2 ≤ 7 d); "expired grant → A1" rule | M15 |
| ST-05 | **One agent compromised or misbehaving** | No write creds; C13/C04 still gate; rate limits, budgets; quarantine taint blocks R1+ from untrusted inputs; contradictions trigger demotion; owner revokes key (registry) → all cells reject its envelopes; C06 cancels pending actions | **Yes** | Explicit "revocation cancels pending actions" in C06 | M6, M8 |
| ST-06 | **Agent proposes an action it lacks permission for** | C04 `deny` naming the missing grant; audit records attempt; repeated denied attempts counted toward demotion; agent may file `grant.request` (R0) | **Yes** | Effect-grouping in C13 so renamed actions can't evade a deny | M5 |
| ST-07 | **Operation partially completes then fails** | C08 compensations in reverse; irreversible steps last; failed compensation → `needs_owner`; C06 `unknown` → reconciliation by `read_state` | **Yes** (C08 initially minimal) | Ordering rule for irreversible steps | M20 (C06 part M5) |
| ST-08 | **Owner revokes an agent's authority while it runs** | C05 broadcasts `authority.revoked`/`system.suspended`; C11 stops agent; C06 checks revocation before each adapter call and between steps; non-cancel-safe in-flight call completes, nothing further starts; Telegram path works without hub | **Yes** | `cancel_safe` declared per adapter operation | M6 |
| ST-09 | **Component replaced with a different implementation** | Same `component_id` or new one with same capabilities; conformance suite passes; registry swap; data migration is the component's own job (no one else reads its DB) | **Yes** | NFR-011; capability-based addressing for jobs (`research.summarise@1`, not agent names) | M3 |
| ST-10 | **Independent application uses Autonomy services without surrendering data or logic** | App registers as `app:<name>`; uses C07 jobs, C12 model routing, C05 owner notifications; keeps own DB, auth, customers; private C15 space if used; `privacy` honoured in sync | **Yes** | Applications are explicitly outside governance for their *own* customers' actions; only actions on owner resources go through C06 | M22 |
| ST-11 | **Two agents disagree about an important decision** | Proposals on the same resource within the conflict window → both escalated; C13 shows both rationales; owner or a priority policy decides; outcome stored as precedent | **Yes** | No LLM voting; GOV-009 conflict window | M8 |
| ST-12 | **Controller reports success but intended result did not happen** | C06 records `succeeded` (adapter accepted) but C14 checks independent observation (flow meter during run, soil wet-up after) → `contradicted` → incident, undo proposal, demotion check; farm hardware no-flow detection already exists [V] | **Yes** | `succeeded` ≠ outcome (contracts §8.7); every actuation must declare a probe | M8, M13b |
| ST-13 | **Owner migrates away from a cloud or model provider** | Model: C12 routing config + agent eval suites. Cloud: components are containers/binaries with SQLite/Postgres; no managed-service dependency in the core; `jarvis` on GCP is an application and can move (its own Terraform) | **Yes** | NFR-010 export formats; no cloud-specific APIs in C01–C21 | M16 |
| ST-14 | **Recover after serious infrastructure failure** | Off-site backups (C19), registry/policy repos, release tags, owner root key; runbooks; cells continue locally during hub rebuild; audit continuity verified | **Partially** until M16 drills exist | Quarterly drill requirement (OPS-002); root key custody procedure | M16 |

## Gaps found and how they were closed

| Gap revealed by scenario | Fix in the design |
|---|---|
| ST-03: early drafts let the evaluator depend on an LLM | LLM critique is optional and escalate-only (ADR-008) |
| ST-04: revocations cannot reach offline cells | Short grant expiries per risk class; offline expiry drops to A1 |
| ST-06: an agent could retry a denied action under another name | C13 groups actions by declared effect |
| ST-08: cancelling mid-call could be unsafe for some adapters | Adapters declare `cancel_safe` |
| ST-12: "success" ambiguity | Separate ExecutionRecord and Verification contracts |
| ST-14: recovery untested | Drills are a milestone deliverable, not documentation |

## Integration test catalog (IT)

Referenced from [requirements.md](requirements.md). Each runs in the CI of the component(s) named, or as a staged test at the milestone.

| Id | Proves | Where | Milestone |
|---|---|---|---|
| IT-01 | Manifest validates; no `reads_from` points at another component's store | `autonomy-conformance` in every repo | M1 |
| IT-02 | Envelopes signed in Python verify in Go and vice versa; tampered, replayed, expired and unknown-sender envelopes are rejected | C02 | M2 |
| IT-03 | `/v1/health` and `/v1/ready` report failed checks with `impact` | All long-running components | M2 |
| IT-10 | Agent output is a valid `Plan` of `ActionProposal`s; no adapter write call originates from an agent process | C11, A01 | M7 |
| IT-11 | PDP: default deny, R3 always denied, untrusted R1+ → approval, grant bounds enforced; identical results in Go and Python | C04 | M5 |
| IT-12 | Approval bound to `params_hash`: any parameter change, expiry, wrong operator or group chat invalidates it | C05 | M6 |
| IT-13 | Write credentials exist only in C06's container; an agent cannot reach the adapter write endpoint | C06, C11, C20 | M5 |
| IT-14 | Duplicate `idempotency_key` returns the original job/action; same key with different params → `conflict.idempotency_mismatch` | C06, C07 | M4, M5 |
| IT-15 | Rate limits and circuit breaker stop the third automatic action per target per hour and the second failure in 24 h | C06 | M5 |
| IT-20 | Job lifecycle with leases, retries, DLQ and worker kill mid-job; worker connects outbound only | C07 | M4 |
| IT-21 | `traceparent`/`correlationid` from submitter appear in worker and executor logs and spans | C07, C17 | M4 |
| IT-40 | Every knowledge query result carries provenance, trust and validity | C15 | M19 |
| IT-41 | Space isolation: an agent cannot read another agent's private space; deleting a space removes its file | C15 | M19 |
| IT-42 | A verified action produces an outcome record linked to its proposal | C14, C15 | M19 |
| IT-51 | Changing a prompt or model route changes `config_hash` in subsequent proposals and audit records | C11, C12 | M7 |
| IT-60 | HA adapter: allowlisted entities readable, others refused; write operations unavailable outside C06 | C20 | M10 |
| IT-70 | Console shows pending approvals, grants, actions with reasons and evidence, component health | C21 | M6 |
| IT-71 | One action story reconstructs end to end from audit records alone, and as one trace | C16, C17 | M6, M9 |
| IT-72 | Owner challenge raises the required level for that decision type immediately on all reachable cells | C05, C04 | M6 |
| IT-80 | Port scan from the internet finds nothing except the VPS Job Service API over TLS | C10, C07 | M9 |
| IT-81 | Secret scanner finds no secrets in git, logs, envelopes or recorded prompts | C18, C17 | M9 |
| IT-82 | Deployed versions match CI-built, signed release tags | All | M9 |
| IT-83 | With egress blocked except to local hosts, `local_only` requests succeed and no request leaves the cell | C12 | M7 |
| IT-90 | Cell Runtime on Pi 5 stays within 300 MB RAM and 5 % average CPU over 24 h | C10 | M10 |
| IT-91 | Evaluator + PDP decision latency < 200 ms p95 on the Pi | C13, C04 | M8 |
| IT-92 | Monthly report contains unattended days, verified outcomes, contradictions, interventions, cost | C17, C21 | M13 |
| IT-93 | A cell upgrade that fails readiness rolls back automatically within 5 min | C10 | M14 |
