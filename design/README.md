# Autonomy — Design Package

**Status:** Draft 0.1 · 2026-11-06 · Temporary name: *Autonomy* · Location: [`realsystem/autonomy`](https://github.com/realsystem/autonomy) `design/` ([ADR-014](adr/ADR-014-design-package-location.md))

> The Field Station's own monitoring and irrigation design lives in [`farm_automation/docs/architecture.md`](https://github.com/realsystem/farm_automation/blob/main/docs/architecture.md); this package treats it as the first reference deployment.

## Purpose

Autonomy is a system assembled from independently developed components that progressively takes over ongoing responsibilities — observing, planning, deciding within granted authority, acting, verifying and recovering — so the owner spends less attention while keeping full control of goals, permissions, data and shutdown.

## The system concept in one paragraph

Autonomy is a **federation of cells bound by contracts**. Each machine or site is a cell that holds a verified copy of the owner-signed registry and policy, decides locally, executes consequential actions only through its own **Action Gateway**, keeps its own event log and tamper-evident audit chain, and syncs with other cells when it can. Agents **propose**; an Evaluator and a Policy Decision Point **decide**; the owner **approves** what policy reserves for them; the gateway **executes**; a Verifier checks **independently** whether the world actually changed. No central orchestrator, broker or LLM sits in any safety path.

## Major blocks

| Group | Components |
|---|---|
| Contracts & foundation | C01 Contracts · C02 Core Library · C03 Identity & Registry |
| Governance & human control | C04 Policy & Authority · C05 Approval & Owner Control · C13 Evaluator · C21 Human Interfaces |
| Execution | C06 Action Gateway · C07 Job Service · C08 Workflow Coordinator · C14 Verifier |
| Intelligence | C11 Agent Host · C12 Model Gateway · agents A01 Field Station, A02 Fleet Doctor, A03 Research |
| Runtime & cross-cutting | C09 Event Log & Messaging · C10 Cell Runtime · C15 Knowledge · C16 Audit · C17 Observability · C18 Secrets · C19 Backup & Recovery · C20 World Adapters |
| Independent applications | P01 leadgen · P02 wildwatch · P03 gardening-service · P04 my_fin_structure · P05 common_reflection · P06 knowledge (YouTube answers) · P07 tires/wires · P08 talk · P09 yt_sync · P10 jarvis |

How they fit: [architecture.md](architecture.md) · [diagrams/logical-architecture.mmd](diagrams/logical-architecture.mmd) · [diagrams/deployment-topology.mmd](diagrams/deployment-topology.mmd).

## What already exists (verified)

| Asset | Becomes |
|---|---|
| `edge_server` — Ed25519 node identity, signed messages, outbox, fleet registry, approval and tier design, power stages, ~800 tests | C10 Cell Runtime; seed of C02, C03, C05, C06 |
| `processor` — durable SQLite job queue with leases, retries, DLQ, NAT-friendly workers | C07 Job Service |
| `jarvis` — provider-independent `LLMProvider` port, voice assistant | C12 seed; voice channel (C21/P10) |
| `farm_agent` — HA add-on prototype (with defects) | A01 Field Station Agent |
| `farm_automation` — layered farm safety design | Field Station reference deployment |
| Media tools (`youtube-processor`, `tts`, `knowledge`, `video_reprocessor`) | Job executors |

Details: [current-state-inventory.md](current-state-inventory.md) · [repository-mapping.md](repository-mapping.md).

## What is missing

Shared contracts, a policy engine and grants, a single governed execution path, independent evaluation and verification, an event log beyond node heartbeats, a knowledge store with provenance (the `knowledge` repo is a YouTube bot), unified audit, tracing, prompt-injection containment, backups and recovery drills, and an emergency stop that spans components. See the gap table in [architecture.md §2](architecture.md#2-gap-analysis).

## Recommended development order

1. **Foundation:** C01 contracts v0.1 → C02 core library (extracted from `edge_server`) → C03 registry.
2. **Minimal core:** C04 PDP + C06 gateway + C16 audit → C05 approvals → C12 model gateway + C11 agent host → C13 evaluator + C14 verifier. Job Service v2 (C07) in parallel.
3. **Field Station:** read-only cell → daily assessments → proposals → one approved, verified helper write → irrigation climate factor after farm commissioning.
4. **Distributed:** multi-cell sync, 7-day offline drill, backups and restore drills, media executors, Fleet Doctor.
5. **Expansion:** knowledge service and research agent, workflows, voice actions via the gateway, applications as consumers, earned higher autonomy.

Details: [development-roadmap.md](development-roadmap.md) · [integration-strategy.md](integration-strategy.md).

## Documents

| Document | Contents |
|---|---|
| [vision-and-principles.md](vision-and-principles.md) | Purpose, principles as constraints, evidence tags |
| [requirements.md](requirements.md) | FR / GOV / SEC / NFR / OPS requirements with links |
| [current-state-inventory.md](current-state-inventory.md) | 26 repositories analysed |
| [repository-mapping.md](repository-mapping.md) | Classification and target role per repository |
| [architecture.md](architecture.md) | Gap analysis, logical and deployment architecture, integration model |
| [component-catalog.md](component-catalog.md) | C01–C21, A01–A03, P01–P10 with boundaries and acceptance |
| [component-contracts.md](component-contracts.md) | **The interoperability contract**: identity, manifest, envelope, action lifecycle, jobs, audit, versioning |
| [agent-model.md](agent-model.md) | Agent lifecycle, prompt-injection containment, delegation, memory, evaluation |
| [governance-and-authority.md](governance-and-authority.md) | Autonomy levels A0–A5, can/may/should/did, Cedar policies, approvals, emergency stop |
| [execution-and-workflows.md](execution-and-workflows.md) | Jobs vs actions vs workflows, `processor` refactor, idempotency, compensation |
| [knowledge-and-memory.md](knowledge-and-memory.md) | Five data kinds, fact model, spaces, learning loop, retention |
| [distributed-runtime.md](distributed-runtime.md) | Cells, enrollment, offline operation, sync, placement |
| [security-and-trust.md](security-and-trust.md) | Threat model, identity, revocation, sandboxing, secrets |
| [observability-and-operations.md](observability-and-operations.md) | Signals, metrics, dashboards, backup and recovery runbooks |
| [external-integrations.md](external-integrations.md) | Adapter contract, Home Assistant and other adapters, MCP/A2A |
| [technology-evaluation.md](technology-evaluation.md) | Choices, alternatives, costs, lock-in |
| [research-and-references.md](research-and-references.md) | Sources and what could not be verified |
| [integration-strategy.md](integration-strategy.md) | Smallest foundation, parallel tracks, per-block plan |
| [development-roadmap.md](development-roadmap.md) | Stages and milestones M0–M23 |
| [field-station-reference-deployment.md](field-station-reference-deployment.md) | First reference deployment, step by step |
| [architecture-tests.md](architecture-tests.md) | The 14 scenario tests and the design changes they forced |
| [risks-and-open-questions.md](risks-and-open-questions.md) | Risks, owner decisions, unresolved questions |
| [adr/](adr/README.md) | 14 architecture decision records |
| [diagrams/](diagrams/) | Mermaid sources: system context, logical, dependencies, deployment, agent lifecycle, execution lifecycle, authority model, action state machine |

## Answers to the three questions

1. **What is the complete system?** A federation of cells that observe, plan, get authorization, act and verify through shared, versioned, signed contracts, with authority granted by the owner and enforced locally.
2. **What building blocks?** Three foundation pieces (contracts, core library, registry), a governed action path (policy, evaluator, approval, gateway, verifier), execution (jobs, workflows), intelligence (agent host, model gateway, narrow agents), runtime and cross-cutting services, and adapters — with existing products kept as independent applications.
3. **In what order?** Contracts → core library → registry → policy + gateway + audit → approvals → models + agent host → evaluator + verifier → Field Station loop → distribution → expansion. Every stage ends with something the owner uses.
