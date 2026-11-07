# Requirements

**Status:** Draft 0.1 · 2026-11-06

Requirement ids are stable. Each links to components ([catalog](component-catalog.md)), decisions ([ADRs](adr/README.md)), tests (IT = integration test, ST = scenario test in [architecture-tests.md](architecture-tests.md)) and milestones ([roadmap](development-roadmap.md)).

Priority: **M** must (before Stage 3 is accepted), **S** should (before Stage 4), **C** could (Stage 5).

## 1. Functional requirements

| Id | Requirement | Pri | Components | Decision | Test | Milestone |
|---|---|---|---|---|---|---|
| FR-001 | Every component publishes a manifest declaring identity, version, contracts, capabilities, data owned and dependencies | M | C01, all | ADR-002 | IT-01 | M1 |
| FR-002 | A component can be registered by an owner-signed registry update without modifying any other component | M | C03 | ADR-003 | ST-01 | M3 |
| FR-003 | All inter-component messages use the signed envelope | M | C01, C02 | ADR-002 | IT-02 | M2 |
| FR-004 | Components expose health and readiness with impact statements | M | All | — | IT-03 | M2 |
| FR-005 | Capabilities are declared with version and scope; consumers resolve providers through the registry | M | C03 | ADR-003 | IT-01 | M3 |
| FR-006 | Contract versions are checked for compatibility at registration time | S | C01, C03 | ADR-002 | ST-09 | M3 |
| FR-010 | Agents submit plans as structured `ActionProposal` lists, never as direct calls to adapters | M | C11 | ADR-005 | IT-10 | M7 |
| FR-011 | Every proposal is evaluated (C13) and decided (C04) before execution | M | C13, C04 | ADR-005 | IT-11 | M8 |
| FR-012 | Gated actions are presented to the owner with rationale, inputs, expected effect and undo, and execute only after a signed approval bound to the exact parameters | M | C05 | ADR-005 | IT-12 | M6 |
| FR-013 | Only the Action Gateway executes R1+ actions | M | C06 | ADR-005 | IT-13 | M5 |
| FR-014 | Every actuation declares an expected effect and a verification probe; results are verified independently where an independent path exists | M | C14, C20 | ADR-005 | ST-12 | M8 |
| FR-015 | Actions are idempotent by `idempotency_key`; a duplicate request returns the original record | M | C06, C07 | — | IT-14 | M5 |
| FR-016 | Partially completed workflows run declared compensations in reverse order | S | C08 | ADR-006 | ST-07 | M20 |
| FR-020 | Durable jobs with leases, retries, timeouts, DLQ, cancellation and typed, versioned job types | M | C07 | ADR-006 | IT-20 | M4 |
| FR-021 | Jobs and actions carry `correlationid` and `traceparent` end to end | M | C07, C06, C17 | — | IT-21 | M4 |
| FR-022 | Workers run on any cell and connect outbound only | M | C07 | ADR-006 | IT-20 | M4 |
| FR-030 | Each cell keeps an append-only event log and forwards to peers with store-and-forward | M | C09 | ADR-007 | ST-04 | M14 |
| FR-031 | Events are deduplicated by `(source, seq)` after any partition | M | C09 | ADR-007 | ST-04 | M14 |
| FR-040 | Knowledge facts carry source, retrieval time, trust, validity interval, confidence and version | S | C15 | ADR-010 | IT-40 | M19 |
| FR-041 | Knowledge spaces are isolated (shared, per-agent, per-application private) with deletion | S | C15 | ADR-010 | IT-41 | M19 |
| FR-042 | Operational outcomes (verified actions) are recorded as learnable history linked to proposals | S | C14, C15 | — | IT-42 | M19 |
| FR-050 | One model interface with routing by privacy, task class, cost and latency, and fallback chains | M | C12 | ADR-012 | ST-03 | M7 |
| FR-051 | Prompt/agent configurations are versioned; their hash appears in every proposal and audit record | M | C11, C12 | ADR-012 | IT-51 | M7 |
| FR-060 | Home Assistant adapter reads state and events via the WebSocket API and writes only allowlisted services via C06 | M | C20 | — | IT-60 | M10 |
| FR-061 | The Field Station runs read-only first, then recommendations, then proposals, then limited execution, then verification | M | A01, C10 | — | ST-12 | M10–M13 |
| FR-070 | Owner console shows pending approvals, grants, recent actions with reasons and evidence, and component health | M | C21 | — | IT-70 | M6 |
| FR-071 | Owner can ask "why" for any action and receive the chain objective → proposal → evaluation → decision → approval → execution → verification | M | C16, C21 | — | IT-71 | M6 |

## 2. Governance requirements

| Id | Requirement | Pri | Components | Decision | Test | Milestone |
|---|---|---|---|---|---|---|
| GOV-001 | All authority derives from owner-signed grants with expiry | M | C03, C04, C05 | ADR-003, ADR-004 | IT-12 | M5 |
| GOV-002 | The proposing component never holds credentials for the actions it proposes | M | C06, C11 | ADR-005 | IT-13 | M5 |
| GOV-003 | Autonomy level is assigned per responsibility × resource × risk class | M | C04 | ADR-004 | IT-11 | M5 |
| GOV-004 | Default deny: unknown principal, action, resource or missing bundle → deny (except R0 and emergency stop) | M | C04 | ADR-004 | IT-11 | M5 |
| GOV-005 | R3 actions are never executed remotely | M | C04, C06 | ADR-004 | IT-11 | M5 |
| GOV-006 | LLM-based review can only escalate, never permit | M | C13 | ADR-008 | ST-11 | M8 |
| GOV-007 | Actions derived from untrusted inputs require approval at R1+ or are denied | M | C04, C11 | ADR-008 | ST-05 | M8 |
| GOV-008 | Rate limits and circuit breakers per action type and target | M | C06 | — | IT-15 | M5 |
| GOV-009 | Conflicting proposals on the same resource are detected and the later one escalated | S | C13 | — | ST-11 | M8 |
| GOV-010 | Every consequential operation has a traceable objective, actor, decision, execution record and outcome evidence | M | C16 | — | IT-71 | M5 |
| GOV-020 | Owner can revoke any grant; revocation takes effect at the next decision on every reachable cell, and at grant expiry on unreachable cells | M | C05, C04 | ADR-003 | ST-08 | M6 |
| GOV-021 | Owner can suspend an agent, a component or the whole system; suspension cancels pending actions and works locally on each cell without the hub | M | C05, C06, C10 | — | ST-08 | M6 |
| GOV-022 | Emergency stop does not depend on any LLM or cloud service | M | C05, C10 | — | ST-03 | M6 |
| GOV-023 | Owner can challenge a decision; a challenged decision type can be reclassified (raise required level) immediately | S | C05, C04 | — | IT-72 | M6 |
| GOV-024 | Agents cannot modify their own grants, policies, prompts or budgets | M | C04, C11 | — | IT-13 | M5 |

## 3. Security requirements

| Id | Requirement | Pri | Components | Decision | Test | Milestone |
|---|---|---|---|---|---|---|
| SEC-001 | Every component has an Ed25519 identity registered by the owner; private keys never leave the host | M | C02, C03 | ADR-003 | IT-02 | M2 |
| SEC-002 | All envelopes are signed and verified, with replay and freshness checks | M | C02 | ADR-003 | IT-02 | M2 |
| SEC-003 | No inbound internet ports on any cell except the hub's public Job Service API behind TLS | M | C10, C07 | ADR-011 | IT-80 | M9 |
| SEC-004 | Secrets are referenced, never embedded; none in git, logs, envelopes or prompts | M | C18 | ADR-013 | IT-81 | M9 |
| SEC-005 | Adapter credentials are scoped per adapter and held only by C06 for write operations | M | C20, C06 | ADR-005 | IT-13 | M5 |
| SEC-006 | Quarantined agents have no consequential tools | M | C11 | ADR-008 | ST-05 | M7 |
| SEC-007 | Releases come from CI-built, signed tags; deployments pin versions | S | All | ADR-011 | IT-82 | M9 |
| SEC-008 | A compromised component can be revoked by registry update, and every cell rejects its envelopes after receipt | M | C03 | ADR-003 | ST-05 | M3 |
| SEC-009 | Private data marked `local_only` never leaves the cell (model routing, sync filters) | M | C12, C09 | ADR-012 | IT-83 | M7 |

## 4. Non-functional requirements

| Id | Requirement | Pri | Components | Test | Milestone |
|---|---|---|---|---|---|
| NFR-001 | No component reads another component's database | M | All | IT-01 | M1 |
| NFR-002 | Each cell continues local observation, policy enforcement, local actions and alerting with no WAN for ≥ 7 days | M | C10, C09, C04, C06 | ST-04 | M15 |
| NFR-003 | Loss of the hub does not stop any cell's safety functions or stored schedules | M | C10 | ST-14 | M15 |
| NFR-004 | Loss of all cloud LLMs degrades intelligence only; local models cover R0/R1 assessments | M | C12 | ST-03 | M7 |
| NFR-005 | Safety mechanisms (pump interlock, valve programs, floats) never depend on Autonomy | M | Field Station hardware | ST-12 | M10 |
| NFR-006 | Cell Runtime runs on a Raspberry Pi 5 within 300 MB RAM and < 5 % average CPU | M | C10 | IT-90 | M10 |
| NFR-007 | Decision latency (C13 deterministic + C04) < 200 ms p95 on the Pi | S | C13, C04 | IT-91 | M8 |
| NFR-008 | Event sync after 7-day partition completes within 30 min on Starlink | S | C09 | ST-04 | M15 |
| NFR-010 | All data is exportable in open formats; no vendor account required to run, enforce or stop | M | All | ST-13 | M16 |
| NFR-011 | Any component can be replaced by another implementation passing the same conformance level | M | C01 | ST-09 | M3 |
| NFR-020 | Owner attention is measured (approvals/week, alerts/week, time-to-approve) | S | C21, C17 | IT-92 | M13 |

## 5. Operational requirements

| Id | Requirement | Pri | Components | Test | Milestone |
|---|---|---|---|---|---|
| OPS-001 | One trace and one audit chain per action story | M | C16, C17 | IT-71 | M9 |
| OPS-002 | Backups of every store, keys and configuration, with restore tested quarterly | M | C19 | ST-14 | M16 |
| OPS-003 | Every automatic repair is reversible, allowlisted, rate-limited, verified, and stops after two failures in 24 h (from `edge_server` [V]) | M | C06, A02 | IT-15 | M18 |
| OPS-004 | Upgrades are rolling per cell with automatic rollback on failed readiness | S | C10 | IT-93 | M14 |
| OPS-010 | Monthly report: unattended days, verified outcomes, failed/contradicted verifications, owner interventions | S | C17, C21 | IT-92 | M13 |
