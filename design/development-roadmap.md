# Development Roadmap

**Status:** Draft 0.1 · 2026-11-06 · No calendar dates; complexity: **S** (days), **M** (1–3 weeks), **L** (1–2 months) of part-time work [A]

Milestone ids (M0–M23) are referenced from [requirements.md](requirements.md).

## Stage 0 — Discovery

### M0 Design package (this document set) — done
- **Deliverables:** inventory, mapping, architecture, contracts, ADRs. **Usable:** a shared plan.

## Stage 1 — Integration foundation

| Milestone | Objectives | Components | Prerequisites | Deliverables | Acceptance | Risks | Testing | Usable at end | Size |
|---|---|---|---|---|---|---|---|---|---|
| **M1** Contracts v0.1 | Freeze first schemas | C01 | M0 | `spec/` folder in `realsystem/autonomy`: schemas, fixtures, conformance CLI | All fixtures validate; compat checker runs in CI | Over-designing schemas | Schema tests | Any repo can validate its messages | M |
| **M2** Core library v0.1 | One envelope implementation | C02 | M1 | Python package extracted from `edge_server` controlplane; Go module | `edge_server` heartbeats use it, ~800 existing tests green; Python↔Go vectors pass | Breaking `edge_server` | Existing suite + vectors | Signed envelopes everywhere | M |
| **M3** Registry v0.1 | Identity for all components | C03 | M2 | Registry schema; `autonomy registry` CLI; `fleet.yaml` migration | New mock component accepted by a cell with no code change (ST-01) | Migration friction | CLI tests | Owner-signed registry for fleet + components | M |
| **M4** Job Service v2 contract | Make `processor` an Autonomy component | C07 | M1, M2 (Go) | J1–J4, J9 from execution doc; manifest; CI | Idempotency, trace propagation, signed worker identity (IT-14, IT-20, IT-21) | Regressions without existing CI | New CI + integration scripts | Media apps can submit traced, idempotent jobs | M |

## Stage 2 — Minimum viable Autonomy Core

| Milestone | Objectives | Components | Prerequisites | Deliverables | Acceptance | Risks | Testing | Usable at end | Size |
|---|---|---|---|---|---|---|---|---|---|
| **M5** PDP + Action Gateway + Audit | Single governed execution path | C04, C06, C16 | M3 | Cedar PDP embedded; gateway with mock + `edge_server` host-helper adapter; audit chain | No R1+ action without decision; crash reconciliation (IT-13, IT-15) | Policy complexity | Policy unit tests; fault injection | `edge_server` L1 repairs run through the gateway | L |
| **M6** Approval & owner control | Owner in the loop | C05, C21 (Telegram) | M5 | Approval flow (generalised from `edge_server` design [V]); revoke/suspend/hold | Approval binding and expiry (IT-12); suspend works offline (ST-08) | Approval fatigue | Replay/tamper tests | Gated `/reboot`/`/redeploy` via approvals | M |
| **M7** Model gateway + agent host | Provider-independent agents | C12, C11 | M2 | C12 library from `jarvis` port; Ollama + 2 cloud adapters; C11 thin loop; budgets | `local_only` never egresses (IT-83); provider outage falls back (ST-03) | Scope creep into a framework | Fake providers; egress block test | `jarvis` and A01 share one model layer | M |
| **M8** Evaluator + Verifier | "Should" and "did it work" | C13, C14 | M5, M7 | Deterministic checks; LLM critique (escalate-only); verification probes | Stale inputs never `ok`; contradicted detection (ST-12) | False confidence in LLM review | Recorded scenarios | Evaluations and verifications in audit | M |
| **M9** Observability + secrets baseline | Diagnosable, no leaks | C17, C18 | M2 | Logs/trace conventions; hub collector; `secret://`; SOPS/age; secret scanning | One trace per action story (IT-71); no secrets found (IT-81) | Hub resource use | Scanner in CI | Owner can trace an action end to end | M |

## Stage 3 — First complete autonomous workflow (Field Station)

Depends on farm_automation Phases 1–3 (monitoring baseline, frozen) [V]. Irrigation *actions* additionally depend on farm Phase 4 commissioning.

| Milestone | Objectives | Components | Prerequisites | Deliverables | Acceptance | Risks | Testing | Usable at end | Size |
|---|---|---|---|---|---|---|---|---|---|
| **M10** Read-only Field Station cell | Observe without risk | C10 (ha-addon), C20 HA adapter, C09 | M2, M3; farm Phase 2 | Add-on with HA WebSocket read adapter, allowlist from `farm/farm_core.yaml`, event log, heartbeats | No writes possible (no write creds mounted); HA config untouched; survives HA restart (IT-60, IT-90) | Disturbing working HA | HA dev container; HA backup before install | Farm events in the event log, visible remotely | M |
| **M11** A01 analysis & recommendations | Useful daily insight | A01, C11, C12, C15-lite (agent memory) | M7, M10 | Refactored `farm_agent`: daily assessment, owner Q&A, recommendations as text | Golden scenarios pass; owner rates usefulness ≥ 80 % over 14 days | Hallucinated values | Recorded data; adversarial tests | Daily Telegram assessment | M |
| **M12** Proposed action plans | Plans in the governed format | A01, C13, C04 | M8, M11 | Plans with proposals for recommendation helpers; evaluations; decisions (no execution yet, `dry_run`) | Every proposal has inputs, expected effect, verification; ≥ 95 % pass deterministic checks | Noisy proposals | Shadow mode | Owner sees what *would* be done | S |
| **M13** Limited authorized execution + verification | First real governed action | C06, C05, C14 | M6, M12 | A2 grant: `ha.helper.set` on recommendation helpers (R1); owner approval via Telegram; verification; monthly report | Full chain in audit for every action; 30 days with 0 unexplained actions (NFR-020, OPS-010) | Owner fatigue | Live, supervised | **First complete loop: observe → … → verify** | M |
| *M13b* (after farm Phase 4) | Irrigation climate factor via coordinator | A01, C06, C20 | M13 + farm Phase 4 exit | R2 operation `opensprinkler.water_level.set` within farm clamps, approval required (A2) | Verification by next-run flow volume and soil wet-up; contradiction raises incident | Physical impact | Supervised wet testing per farm §7.7 [V] | ET₀ climate factor applied with owner approval | M |

## Stage 4 — Distributed operation

| Milestone | Objectives | Components | Deliverables | Acceptance | Size |
|---|---|---|---|---|---|
| **M14** Multi-cell federation | Several cells, one system | C09 sync, C10 | Sync protocol; home-hub, vps, field-station, gpu-pc cells; rolling upgrades | Registry/bundle hash agreement; OPS-004 rollback works | L |
| **M15** Offline drill | Prove local-first | C10, C09, C04, C06 | 7-day WAN cut at Field Station (planned, announced) | ST-04 passes: no loss, no duplicates, local R1 continued, R2 waited and expired | M |
| **M16** Backup & recovery | Survive serious failure | C19 | Litestream/backups, runbooks, quarterly drill | ST-14: hub rebuilt < 4 h from off-site copies | M |
| **M17** Workers & media executors | Shared compute | C07 workers, media executors | Executors from `youtube-processor`, `tts`, `knowledge`, `video_reprocessor` | Each executor passes conformance; jobs placed by capability | M |
| **M18** Fleet Doctor | The doctor is never the patient | A02 | Incident agent per `edge_server` design via C06 | OPS-003; diagnoses another cell; circuit breaker works | M |

## Stage 5 — Expansion

| Milestone | Objectives | Components | Acceptance | Size |
|---|---|---|---|---|
| **M19** Knowledge service + Research agent | Knowledge with provenance | C15, A03 | Provenance on every fact; quarantine respected (ST-05) | L |
| **M20** Workflow coordinator | Multi-step with compensation | C08 | ST-07 | M |
| **M21** `jarvis` tools via gateway | Voice can request actions safely | P10/C21, C06 | Voice request → proposal → approval → action | M |
| **M22** Applications as consumers | Apps use platform services | P01–P09 | At least two apps use C07/C12 without sharing data | M |
| **M23** Adaptive autonomy | Higher levels where earned | C04, A01 | Promotion evidence per §2 of governance doc; A3 for farm reporting; A4 only with numeric bounds and independent verification | M |

## Critical path

`M1 → M2 → M3 → M5 → M6 → (M7 ∥ M10) → M8 → M11 → M12 → M13`

M4 (Job Service) and M9 (observability) run in parallel and are needed by Stage 4, not by M13.
