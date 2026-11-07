# Vision and Principles

**Status:** Draft 0.1 · 2026-11-06 · Part of the [Autonomy design package](README.md)

## Evidence tags used throughout this package

| Tag | Meaning |
|---|---|
| **[V]** | Verified by inspecting a repository (commit inspected on 2026-11-06) |
| **[R]** | External research finding, with a source in [research-and-references.md](research-and-references.md) |
| **[D]** | Design recommendation made in this package |
| **[A]** | Assumption that has not been verified |
| **[Q]** | Open question; see [risks-and-open-questions.md](risks-and-open-questions.md) |

---

## 1. What Autonomy is

Autonomy is a set of independently built components that together let one owner hand over ongoing responsibilities: watching a remote farm, running media pipelines, maintaining infrastructure, researching a topic. The owner should spend less attention over time without losing control.

It is **not** a chatbot, a single agent framework or a home-automation hub. Those are parts of it, or things it connects to.

The system is defined by its **contracts**, not by a central program. A component belongs to Autonomy when it implements the contracts in [component-contracts.md](component-contracts.md): identity, manifest, envelope, events, action requests, jobs, health and audit. Any implementation that meets them can replace another.

## 2. The three questions this package answers

1. **What is the complete system?** A federation of *cells* (one per site or machine) that observe, plan, get authorization, act and verify through shared contracts, with authority enforced locally in each cell and granted by the owner. See [architecture.md](architecture.md).
2. **What building blocks are required?** Twenty-one platform components (C01–C21), three specialized agents (A01–A03), and independent applications (P01–P09). See [component-catalog.md](component-catalog.md).
3. **In what order?** Contracts and a small core library first, then authority and execution, then the Field Station as the first end-to-end workflow, then distribution, then expansion. See [development-roadmap.md](development-roadmap.md).

## 3. Design principles as constraints

Each principle is written as a testable constraint and linked to requirements in [requirements.md](requirements.md).

| # | Principle | Constraint it imposes | Requirements |
|---|---|---|---|
| P1 | **Owner sovereignty** | Every identity, policy and grant traces back to an owner root key. No component may require a vendor account to start, enforce policy or stop. Data is stored in open formats (SQLite, Postgres, JSON, Markdown) the owner can copy | SEC-001, GOV-001, NFR-010 |
| P2 | **Useful autonomy, measured by outcomes** | Success metrics are owner attention minutes per week, unattended-days without incident and verified outcomes — not agent counts | NFR-020, OPS-010 |
| P3 | **Modular construction** | Each component has a manifest, versioned contracts and its own data. No component reads another's database | FR-001…FR-006, NFR-001 |
| P4 | **Distributed operation without a single point of failure** | Each cell can observe, enforce policy and run local actions with no network. The hub coordinates; it is not required for safety or local operation | NFR-002, NFR-003 |
| P5 | **Separate intelligence, authority and execution** | The process that proposes an action never holds the credential that executes it. Only the Action Gateway (C06) executes consequential actions, and only with a valid signed decision | GOV-002…GOV-006 |
| P6 | **Local-first, graceful degradation** | Loss of internet, a cloud provider or an LLM removes intelligence, never safety or existing schedules | NFR-004, NFR-005 |
| P7 | **Verifiability** | Every consequential action has an objective, actor, decision, execution record and independent outcome evidence, linked by one correlation id | GOV-010, OPS-001 |
| P8 | **Human control** | The owner can inspect, explain, revoke, suspend and stop at every level, including while an action is running, and the stop path works without the LLM or the hub | GOV-020…GOV-024 |

### Additional principles from the research and repository review

| # | Principle | Why |
|---|---|---|
| P9 | **Dumb before smart** | Hardware interlocks, controller programs and fixed rules handle known failures. Agents handle what they cannot. Already a principle in `edge_server` [V] and in the farm irrigation design [V] |
| P10 | **The doctor is never the patient** | A failing node or agent is diagnosed by another. From `edge_server` [V] |
| P11 | **LLM judgement can only add caution** | An LLM reviewer may raise an action to "needs approval" or "deny"; it may never be the reason an action is permitted. Panels of LLM judges have strongly correlated errors [R: Apple ML research], so votes do not provide independence |
| P12 | **Untrusted content never steers consequential actions** | Agents that read web pages, emails, transcripts or messages from outsiders run without consequential tools, or under plan-then-execute [R: Beurer-Kellner et al. 2025; CaMeL] |
| P13 | **An API "success" is not an outcome** | Every actuating action declares how its effect will be observed independently, and is not marked successful until that observation arrives |
| P14 | **Build the smallest thing that is useful now** | No broker, workflow server, vector database or orchestrator until a measured need appears (see the revisit conditions in the ADRs) |

## 4. Autonomy as a gradient

Autonomy is granted per **responsibility × resource × risk class**, not per agent. Levels A0–A5 are defined in [governance-and-authority.md](governance-and-authority.md). Every grant has an owner signature, an expiry and a revocation path.

For every action the system records separately:

| Question | Answered by | Record |
|---|---|---|
| **Can do** | Capability declared in the component manifest and present in the registry | `capabilities[]` |
| **May do** | Policy decision from the Policy Decision Point against the active grants | `decision` |
| **Should do** | Evaluator: preconditions, context, risk, optional independent review | `evaluation` |
| **Did it work** | Verifier: independent observation of the declared expected effect | `verification` |

## 5. What success looks like

| Horizon | Observable outcome |
|---|---|
| First end-to-end workflow (Stage 3) | The Field Station agent observes the farm through Home Assistant, writes a daily recommendation, and the owner approves one low-risk change from a phone. The change is executed only by the Action Gateway, verified from sensor data, and appears in one audit trail |
| Distributed (Stage 4) | The farm cell runs for 7 days with no internet; local programs, alerts and policy enforcement continue; on reconnect, events and audit records sync with no loss and no duplicate execution |
| Expanded (Stage 5) | A new agent written in another repository joins by publishing a manifest and getting an owner-signed grant, with no change to any core component |

## 6. Non-goals

- A general consumer assistant product, branding or marketing.
- Full autonomy (A5) for anything that can cause physical harm, spend money or leak private data.
- Replacing Home Assistant, OpenSprinkler or the pump controller's own safety logic.
- Multi-tenant SaaS. Autonomy has one owner; applications like `leadgen` may have their own customers and remain responsible for them.
