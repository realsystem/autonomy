# Component Catalog

**Status:** Draft 0.1 · 2026-11-06 · Contracts referenced here are defined in [component-contracts.md](component-contracts.md)

Identifiers are stable across the package: **C** = platform component, **A** = specialized agent, **P** = independent application. Priority: **P0** foundation, **P1** first workflow, **P2** distribution, **P3** expansion.

## 1. Consolidated catalog

| Id | Component | Existing repo | New / existing | Primary responsibility | Depends on | Public contracts | Independent dev | Priority | Maturity |
|---|---|---|---|---|---|---|---|---|---|
| C01 | Contracts | `autonomy` (`spec/`, new) | New | Schemas, versioning rules, conformance kit | — | All schemas | High | P0 | In progress: [`spec/`](../spec/README.md) v0.1.0 draft |
| C02 | Core Library | `edge_server/telegram-bot/controlplane.py` | Extract | Signing, envelope, outbox, audit append, PDP embedding, telemetry helpers (Python + Go) | C01 | Library API | High | P0 | In progress: [`core/`](../core/README.md) v0.1.0 draft (PDP and telemetry helpers later) |
| C03 | Identity & Registry | `edge_server` `fleet.yaml`, `tools/fleet.py` | Refactor | Owner keys, component/node enrollment, signed registry document | C01, C02 | Registry document, enrollment request | High | P0 | In progress: spec 0.2 registry schemas, `autonomy registry` CLI and verification in [`core/`](../core/README.md); `edge_server` cells verify it (enrollment requests and `GET /registry` later) |
| C04 | Policy & Authority | — | New | Policy Decision Point, grants, autonomy levels, risk classes | C01–C03 | Decision, Grant, policy bundle | High | P1 | In progress: `core` PDP with Cedar (`cedarpy`), spec `policy-bundle` |
| C05 | Approval & Owner Control | `edge_server` approval design, Telegram bot | Refactor | Approval requests, owner approvals, revocations, suspend/e-stop | C02–C04, C21 | Approval, Revocation, Suspend | Medium | P1 | In progress: `core` ApprovalService and ControlState; `edge_server` /approve, /suspend, /hold, /resume |
| C06 | Action Gateway | `edge_server` executor design | New | Sole executor of consequential actions; checks decision; idempotency; rate limits; records | C02, C04, C20 | ExecutionRecord, action API | High | P1 | In progress: `core` ActionGateway; `edge_server` host actions run through it |
| C07 | Job Service | `processor` | Refactor | Durable jobs, leases, retries, DLQ, workers, executors | C01, C02 | Job, worker API | High | P1 | Working MVP |
| C08 | Workflow Coordinator | — | New (library first) | Multi-step workflows with compensation over C07 | C07 | Workflow, Step | Medium | P2 | Not started |
| C09 | Event Log & Messaging | `edge_server` Outbox/SeenStore | Refactor | Per-cell append-only event log, topics, store-and-forward sync | C01, C02 | Envelope, sync API | High | P0 | Partial |
| C10 | Cell Runtime | `edge_server` | Refactor | Per-cell daemon: identity, health, local hosting of C04/C06/C09/C16, updates, power stages | C02–C04, C06, C09 | Health, cell API | Medium | P1 | Partial |
| C11 | Agent Host | `farm_agent` loop (pattern only) | New (ADR-017) | Runs agents: budgets, tool binding, trust isolation, memory access | C02, C12, C06, C15 | Agent manifest, Plan | Medium | P1 | Not started |
| C12 | Model Gateway | — (four direct Ollama clients to replace); local inference via the GPU PC Ollama and `processor` (C07) | New (ADR-017) | Provider abstraction, routing (privacy/cost/latency), fallback, budgets | C02 | `LLMProvider` port, usage events | High | P1 | Not started (planned in `autonomy-core`, ADR-017) |
| C13 | Evaluator | — | New | Preconditions, invariants, risk class, dry-run, optional independent LLM critique | C04, C12 | Evaluation | High | P1 | In progress: `core` Evaluator (deterministic checks, escalate-only LLM review hook); `edge_server` evaluates host actions |
| C14 | Verifier | — | New | Independent outcome verification from declared probes | C20, C09 | Verification | High | P1 | In progress: `core` Verifier (probes, signed verifications, contradicted → incident + undo proposal, ST-12 scenarios); `edge_server` verifies host actions |
| C15 | Knowledge Service | — (`knowledge` repo is unrelated) | New | Documents, facts with provenance and time, private spaces, retrieval | C02, C12 | Knowledge API, fact schema | High | P3 | Not started |
| C16 | Audit Ledger | `processor.audit_log`, `edge_server` incident design | New format | Hash-chained, signed audit records per cell; export | C02 | AuditRecord | High | P0 | In progress: `core` AuditLog fed by the gateway; `/audit` in `edge_server` |
| C17 | Observability | — | New conventions | Logs, traces, metrics, cost; dashboards | C02 | Log/trace conventions | High | P1 | In progress: `core` telemetry (JSON logs, OTLP/JSON traces, redaction, IT-71); hub collector config in `ops/hub/` (OTLP + span files → Jaeger, Prometheus, archive) |
| C18 | Secrets | `edge_server/src/secret_store.py` | Refactor | Per-cell secret store, encrypted config in git, credential scoping | C02 | Secret reference `secret://` | High | P1 | In progress: `core` secret references (env, file, SOPS, age, store), SOPS check, scanner + gitleaks in CI |
| C19 | Backup & Recovery | `edge_server` archive role (design) | New | Backups of SQLite/HA/config/keys, restore drills | C10, C18 | Backup manifest | Medium | P2 | Design only |
| C20 | World Adapters | `farm_agent` HA calls, `edge_server` plugins | Refactor | Scoped access to HA, Frigate, power, web, email, files; observations and actuations with probes | C02, C09 | Adapter contract, observation events | High | P1 | Partial |
| C21 | Human Interfaces | `edge_server` bot | Refactor / reuse | Owner Console (web), Telegram | C05, C09, C16 | UI only (uses others' APIs) | High | P1 | Partial |
| A01 | Field Station Agent | `farm_agent` | Refactor | Farm observation, assessment, irrigation recommendations, proposals | C11, C12, C20, C15 | Agent manifest | High | P1 | Prototype |
| A02 | Fleet Doctor | `edge_server` incident agent (design) | New | Diagnose other cells; propose repairs from catalog | C11, C06 | Agent manifest | High | P2 | Design only |
| A03 | Research Agent | — | New | Research, source evaluation, knowledge maintenance (quarantined: no consequential tools) | C11, C12, C15 | Agent manifest | High | P3 | Not started |
| P01–P10 | Applications | see [repository-mapping.md](repository-mapping.md) | Existing | Domain products using Autonomy services optionally | C07, C12, C09 (optional) | Their own | — | — | Various |

Applications: P01 `leadgen`, P02 `wildwatch`, P03 `gardening-service`, P04 `my_fin_structure`, P05 `common_reflection`, P06 `knowledge` (YouTube answers), P07 `tires`/`wires`, P08 `talk`, P09 `yt_sync`, P10 `jarvis` (an independent application, out of Autonomy's scope since 2026-11-11; ADR-017).

## 2. Component specifications

Each entry lists: responsibility · does not · inputs/outputs · APIs · events · data · dependencies · infrastructure · security boundary · failure modes · testing · deployment · integration · acceptance.

### C01 Contracts (`autonomy/spec`)

| | |
|---|---|
| Responsibility | JSON Schemas (Draft 2020-12) for every contract; semantic-versioning rules; conformance test kit (fixtures + validators) runnable in any repo's CI |
| Does not | Contain runtime code beyond validators |
| I/O | In: proposals for schema changes (PRs). Out: versioned schema releases `vMAJOR.MINOR.PATCH` |
| APIs / events | None (files) |
| Data | Schemas, examples, changelog |
| Dependencies | None |
| Infrastructure | Git repo, CI |
| Security | Release tags signed; schema changes reviewed by owner |
| Failure modes | Breaking change slips into a minor release → CI checks compatibility of every schema against the previous release |
| Testing | Every example validates; backward-compat checker |
| Deployment | Vendored or fetched by version into each component |
| Integration | Each component's CI runs `autonomy-conformance` against its manifest and sample messages |
| Acceptance | v0.1 contains manifest, envelope, job, action lifecycle, health, audit schemas; two components (C07, C10) pass conformance |

### C02 Core Library (`autonomy-core`, Python and Go)

| | |
|---|---|
| Responsibility | Ed25519 key handling; canonical JSON; envelope build/sign/verify; freshness and replay checks; persistent sequence counter; SQLite outbox; audit append (hash chain); embedded Cedar PDP wrapper; `secret://` resolution; OTel/log helpers |
| Does not | Make decisions, store domain data, open network listeners by itself |
| APIs | `Envelope.build/sign/verify`, `Outbox.put/drain`, `Audit.append/verify_chain`, `Pdp.decide(request)`, `Secrets.resolve(ref)` |
| Data | Local files owned by the host component |
| Dependencies | C01; `cryptography` (Python), `crypto/ed25519` (Go), `cedarpy` / `cedar-go` |
| Security | Private keys never leave the host; constant-time signature checks; rejects unknown `specversion` |
| Failure modes | Clock skew breaks freshness → tolerance window + monotonic sequence per sender; key file corruption → atomic writes (already in `edge_server` [V]) |
| Testing | Cross-language test vectors (Python signs, Go verifies and vice versa) |
| Deployment | PyPI-style package from git tag; Go module |
| Acceptance | `edge_server` and `processor` both use it; cross-language vectors pass |

### C03 Identity & Registry

| | |
|---|---|
| Responsibility | Owner root and operational keys; enrollment of cells, components and agents; the **registry document** (generalised `fleet.yaml`) signed by the owner; key rotation and revocation lists |
| Does not | Evaluate policy; issue sessions |
| Inputs | `EnrollmentRequest` (component's public key, manifest hash) |
| Outputs | Signed `registry.yaml` + detached signature; `RevocationList` |
| APIs | CLI `autonomy registry add|revoke|sign|verify`; read-only `GET /registry` from any cell |
| Events | `registry.updated`, `identity.revoked` |
| Data | Registry history in git (owner's private repo), signed |
| Security | Root key offline (hardware key or paper backup); operational key signs day-to-day changes within limits set by root |
| Failure modes | Registry not yet propagated → cells keep last valid version, reject components not present; stale revocation → short grant expiry bounds risk |
| Testing | Schema + signature verification; enrolment of a mock component in CI |
| Deployment | CLI on owner's machine; copies on each cell |
| Acceptance | A new component is accepted by every cell after one signed registry update, with no code change |

### C04 Policy & Authority

| | |
|---|---|
| Responsibility | Evaluate `DecisionRequest` (principal, action, resource, context) against owner-signed policy bundle and active grants; output `permit` / `deny` / `require_approval` plus obligations (rate limit, verification required, expiry) |
| Does not | Execute; judge "should" beyond declared rules; call LLMs |
| APIs | Library `decide()`; optional local HTTP `POST /v1/decide` for non-Python/Go clients |
| Events | `decision.made` (to audit) |
| Data | Policy bundles (Cedar policies + schema), grants (signed), counters for rate limits (local SQLite) |
| Dependencies | C02 (embedded Cedar), C03 (principals) |
| Security | Bundle and grants signature-verified on load; deny by default; unknown action → deny |
| Failure modes | Bundle missing/invalid → deny everything except T0 read-only and emergency actions; counter store lost → reset to conservative (assume limit reached) |
| Testing | Policy unit tests (Cedar test cases), property tests: no permit without matching grant |
| Deployment | Embedded in every C06 instance |
| Acceptance | Same request gives identical decision on every cell holding the same bundle; decisions are reproducible from audit |

### C05 Approval & Owner Control

| | |
|---|---|
| Responsibility | Present approval requests to the owner, collect strong confirmation (TOTP now, passkey/WebAuthn on the console later), sign `Approval`; issue and broadcast `Revocation` and `Suspend` |
| Does not | Decide policy (it only adds the owner's signature where policy requires) |
| APIs | `POST /v1/approval-requests`, `GET /v1/approval-requests/{id}`, owner actions via Telegram `/approve <id> <code>` and console |
| Events | `approval.requested`, `approval.granted`, `approval.denied`, `approval.expired`, `authority.revoked`, `system.suspended` |
| Data | Approval requests and outcomes (SQLite) |
| Security | Approval binds to the hash of the exact action parameters (from `edge_server` design [V]); expiry ≤ 15 min default; private chat only |
| Failure modes | Telegram down → console; console down → Telegram; both down → nothing gated runs (fail closed) while T0/T1 continue |
| Testing | Replay, tamper, expiry, wrong-operator tests |
| Acceptance | Approval for action X cannot authorise X′ with any changed parameter |

### C06 Action Gateway

| | |
|---|---|
| Responsibility | The only component holding credentials for consequential actions on its cell. Accepts `ActionRequest` + `Decision` (+ `Approval`), re-checks policy locally, enforces idempotency, rate limits, circuit breakers, power gate, "never cut your own access"; calls the adapter; writes `ExecutionRecord`; hands verification probe to C14 |
| Does not | Plan, choose actions, or interpret free text |
| APIs | `POST /v1/actions` (idempotent by `action_id`), `GET /v1/actions/{id}`, `POST /v1/actions/{id}/cancel`; MCP server facade for LLM-facing tools |
| Events | `action.accepted`, `action.rejected`, `action.started`, `action.succeeded`, `action.failed`, `action.cancelled` |
| Data | Action table (SQLite), rate-limit counters |
| Security | Runs as separate OS user/container; agents have no direct adapter credentials; verifies all signatures; refuses expired decisions |
| Failure modes | Crash mid-action → on restart, actions in `started` are reconciled by querying adapter state and marked `unknown` until verified; never blindly retried unless action is idempotent by contract |
| Testing | Contract tests with mock adapters; fault injection (crash between call and record) |
| Acceptance | No consequential action in the system bypasses C06 (enforced by credential placement and verified by audit sampling) |

### C07 Job Service (from `processor`)

| | |
|---|---|
| Responsibility | Durable queue for work units (`Job`): submission, priorities, leases, retries with backoff, DLQ, cancellation, results; executor registry with typed, versioned job types |
| Does not | Execute consequential world actions directly — a job that needs one submits an `ActionRequest` to C06 |
| APIs | Existing REST plus: `Idempotency-Key` header, `traceparent`, `job_type@version`, `POST /v1/jobs/{id}/cancel`, `GET /v1/job-types` |
| Events | `job.queued`, `job.started`, `job.succeeded`, `job.failed`, `job.dead_lettered`, `job.cancelled` |
| Data | Existing SQLite schema + `idempotency_key`, `correlation_id`, `principal_id`, `job_type_version` |
| Security | Per-worker Ed25519 identity (replaces shared token); job inputs flagged with trust level |
| Failure modes | Control plane down → workers idle, cells keep local operation; worker dies → lease expiry re-queues (existing [V]) |
| Testing | Existing integration scripts + Go unit tests; add conformance + chaos (kill worker mid-job) |
| Acceptance | Duplicate submission with same key returns the same job; trace id flows from submitter to executor logs |

### C08 Workflow Coordinator

| | |
|---|---|
| Responsibility | Multi-step workflows (DAG of jobs and actions) with per-step state, retries, timeouts, and compensation |
| Does not | Replace C07; it is built on it |
| Initial form | Library + tables inside C07 (`workflows`, `workflow_steps`), driven by a coordinator job |
| Revisit | Adopt DBOS or Temporal when workflows need >10 steps, human waits > 1 day in volume, or complex versioning ([ADR-006](adr/ADR-006-extend-processor-defer-workflow-engine.md)) |
| Acceptance | A 3-step workflow that fails at step 3 runs compensations for steps 2 and 1 and records each |

### C09 Event Log & Messaging

| | |
|---|---|
| Responsibility | Per-cell append-only log of envelopes (SQLite), topic subscriptions for local consumers, store-and-forward replication between cells with per-producer sequence numbers and acknowledgements |
| Does not | Guarantee global ordering (only per-producer) |
| APIs | `POST /v1/events` (publish), `GET /v1/events?topic=&after=` (pull), `POST /v1/sync` (cell-to-cell batch with acks) |
| Data | `events` table; retention by topic class |
| Failure modes | Partition → outbox grows; on reconnect, sync resumes from last ack; duplicates removed by `(source, seq)` |
| Revisit | NATS JetStream with leaf nodes when fan-out, throughput or node count exceeds limits in [ADR-007](adr/ADR-007-signed-http-outbox-now-nats-later.md) |
| Acceptance | 7-day partition test: zero loss, zero duplicates after sync |

### C10 Cell Runtime (from `edge_server`)

| | |
|---|---|
| Responsibility | Host per-cell services (C04 embedded, C06, C09, C16), identity, heartbeats, health, power stages, updates from signed release tags, local Telegram bot |
| Packaging | `host` (Ubuntu, systemd/docker) and `ha-addon` (already planned [V]) |
| APIs | `GET /v1/health`, `/v1/ready`, `/v1/cell` (identity, versions, registry hash), control port for signed envelopes |
| Failure modes | Cell down → peers detect via heartbeat; same-site relay power-cycle (existing design [V]) |
| Acceptance | Field Station HA add-on runs C10 with C06/C09/C16 and survives HA restart and power loss without corruption |

### C11 Agent Host

| | |
|---|---|
| Responsibility | Run agent processes with: identity, budgets (tokens, time, actions/day), tool binding from manifest, **trust isolation** (agents marked `quarantined` get no consequential tools), memory handles, plan submission, kill switch |
| Does not | Grant authority; agents act only through C06 |
| APIs | Agent lifecycle `start/stop/suspend`, `submit_plan`, `tool.invoke` (read tools direct, actions via C06) |
| Events | `agent.started`, `agent.suspended`, `plan.submitted`, `agent.budget_exceeded` |
| Acceptance | Suspending an agent mid-run cancels its pending actions at C06 within one poll interval and its in-flight LLM call is abandoned |

### C12 Model Gateway

| | |
|---|---|
| Responsibility | One interface (`complete(ModelRequest)`, ADR-017) for chat, tools, embeddings, vision; routing by `privacy` (`local_only`, `cloud_ok`), task class, cost, latency; fallback chains; usage accounting |
| Does not | Store prompts beyond telemetry policy; decide authority |
| Data | Usage records; routing config (versioned) |
| Failure modes | Cloud down → local model per routing table; no model available → caller receives `unavailable` and must degrade (never block safety) |
| Acceptance | Switching a task class from cloud to local is a config change; `local_only` requests never leave the cell (tested with network egress block) |

### C13 Evaluator

| | |
|---|---|
| Responsibility | Produce `Evaluation` for an `ActionProposal`: schema validity, preconditions (fresh observations), invariants (e.g. water-level clamp), risk class, dry-run/simulation where adapter supports it, conflict check against other pending actions, optional independent LLM critique |
| Rule | LLM critique may only **raise** the outcome (to `require_approval` or `reject`), never lower it ([ADR-008](adr/ADR-008-llm-review-only-escalates.md)) |
| Acceptance | A proposal with stale inputs (older than its declared freshness) is never evaluated as `ok` |

### C14 Verifier

| | |
|---|---|
| Responsibility | Execute the `verification` probe declared in the action contract after the action, using an **independent** observation path where one exists (flow meter, soil probe, second sensor), and emit `Verification` (`confirmed`, `not_confirmed`, `contradicted`, `inconclusive`) |
| Acceptance | An OpenSprinkler "run zone" reported successful but with no flow is recorded `contradicted` and raises an incident |

### C15 Knowledge Service

| | |
|---|---|
| Responsibility | Store and retrieve documents and structured facts with provenance (`source`, `retrieved_at`, `trust`), validity time (`valid_from/to`), confidence and version; spaces (`shared`, `agent:<id>`, `private:<app>`); deletion and retention |
| Does not | Hold operational state or the event log |
| Acceptance | Every fact returned carries provenance; a deleted space is unrecoverable from the service (backups expire on schedule) |

### C16 Audit Ledger

| | |
|---|---|
| Responsibility | Append-only, hash-chained, signed `AuditRecord` per cell; links objective → proposal → decision → approval → execution → verification by `correlation_id`; export to archive cell |
| Acceptance | Removing or editing any record breaks chain verification; full action story reconstructable from records alone |

### C17 Observability

| | |
|---|---|
| Responsibility | Conventions and exporters: JSON logs with `trace_id`, OTel traces with W3C `traceparent` propagated in envelopes, metrics (health, queue depth, model cost), dashboards on hub |
| Acceptance | One trace shows agent → C13 → C04 → C06 → adapter → C14 for a Field Station action |

### C18 Secrets

| | |
|---|---|
| Responsibility | Secrets referenced as `secret://<cell>/<name>`; stored per cell (root-only file or HA add-on password options [V]); configuration in git encrypted with SOPS + age; scoped credentials per adapter |
| Acceptance | No secret appears in git, logs, envelopes, LLM prompts (scanner in CI + log redaction tests) |

### C19 Backup & Recovery

| | |
|---|---|
| Responsibility | Backup of all SQLite stores (Litestream or periodic snapshot), HA backups, registry and policy repo, encrypted keys; restore runbooks; quarterly drills |
| Acceptance | From total loss of the home hub, a new machine restores Job Service, Knowledge and Console in < 4 h using only off-site copies and the owner's root key |

### C20 World Adapters

| | |
|---|---|
| Responsibility | Scoped access to external systems. Each adapter declares observation streams and actuation operations; each actuation declares `expected_effect` and `verification probe`. Initial: Home Assistant (WebSocket `subscribe_events`, `get_states`, `call_service` [R]), Frigate (events), Victron/Renogy (via `edge_server` plugins), web fetch (quarantined), email (later) |
| Security | Entity/endpoint allowlists in adapter config (Home Assistant tokens are not entity-scoped [A], so scoping must be enforced here); read and write operations separated; write credentials held only by C06's adapter instance |
| Acceptance | An agent cannot read an HA entity outside its allowlist even if it asks |

### C21 Human Interfaces

| | |
|---|---|
| Responsibility | Owner Console (web, mobile-first): goals, grants, pending approvals, audit explorer ("why did this happen"), suspend/stop; Telegram bot per cell (existing [V]); voice only as a possible later client of the same APIs |
| Does not | Contain authority logic — it calls C05/C04/C16 |
| Acceptance | From a phone, the owner can see every action of the last 7 days with its reason and evidence, and suspend any agent in two taps |

## 3. Specialized agents

| Id | Responsibility | Trust class | Max autonomy (initial → target) | Tools |
|---|---|---|---|---|
| A01 Field Station Agent | Observe farm, explain state, produce daily assessment and irrigation recommendations, propose low-risk actions | `trusted-input` (sensor data from own cell) | A1 → A3 for recommendations; A2 for helper writes; never above the farm design's own limits | HA read (allowlist), knowledge read/write (farm space), propose actions |
| A02 Fleet Doctor | Diagnose another cell from read-only diagnostics; propose repairs from catalog | `trusted-input` | A2 (T1 repairs within limits) | `service_status`, `journal_tail`, `disk_health`, `network_routes`; propose `restart_service`, `power_cycle_relay` (from `edge_server` design [V]) |
| A03 Research Agent | Research topics on the web, evaluate sources, maintain knowledge | **`quarantined`** (reads untrusted content) | A3 for knowledge writes into its own space; **no consequential tools ever** | web fetch, knowledge write (own space), summarise |
