# Observability and Operations

**Status:** Draft 0.1 · 2026-11-06

## 1. Signals

| Signal | Purpose | Format | Where | Retention |
|---|---|---|---|---|
| Logs | Debugging | JSON lines with `trace_id`, `correlationid` | Local files/journald; shipped to hub when online | 14 d local, 30 d hub |
| Traces | Cross-component diagnosis | OpenTelemetry (OTLP), W3C `traceparent` in envelopes | OTel Collector on hub (optional), local file exporter on cells | 7 d |
| Metrics | Health, capacity, cost | Prometheus exposition or OTel metrics | Hub Prometheus (already used by `gardening-service` and `talk` [V]) | 90 d |
| Events | Facts about the system and world | Signed envelopes | C09 | See knowledge doc |
| Audit | Accountability | Hash-chained records | C16 | ≥ 5 y |
| Usage/cost | Budgets | `usage.recorded` events from C12 | C09 → hub | 2 y |

Logs and traces may be lost under pressure; audit and events may not.

## 2. Minimum viable stack (Stage 2)

- Every component: JSON logs + OTel SDK with file exporter (works offline), `traceparent` propagation.
- Hub: OTel Collector → Grafana Tempo or Jaeger for traces (Jaeger is already in `talk`'s compose [V]), Prometheus + Grafana for metrics, Loki optional.
- Cells push buffered telemetry when the link is up; under power stage `conserve`, telemetry shipping pauses [V: power gating in edge_server].

LLM-specific telemetry follows OpenTelemetry GenAI semantic conventions where stable. These conventions are still evolving and now live in a dedicated repository [R], so the version is pinned in C17's config and upgrades are deliberate.

## 3. What is measured

| Area | Metrics |
|---|---|
| Cells | up, heartbeat age, registry/bundle hash agreement, power stage, disk, temperature |
| Governance | proposals/day, decisions by effect, approvals pending, time-to-approve, approvals < 3 s after display, denials by reason, hold mode |
| Execution | actions by status, `unknown` count, reconciliation time, rate-limit hits, breaker opens; jobs by status, queue depth, lease expiries, DLQ size |
| Verification | confirmed / contradicted / not_confirmed per action type and agent |
| Agents | runs, tokens, cost, budget utilisation, eval-suite pass rate per version |
| Models | latency p50/p95 by provider, fallbacks, `unavailable` responses |
| Owner attention | alerts/week, approvals/week, minutes in console (NFR-020) |

## 4. Dashboards and reports

1. **System health** — every cell and component readiness with `impact`, path state (Tailscale, LAN, Telegram), versions, hash agreement.
2. **Authority** — active grants with expiry, autonomy level per responsibility, pending approvals, recent denials.
3. **Actions** — last 7 days with proposal → decision → execution → verification; filter by agent, cell, result.
4. **Agents** — cost, budget, outcome quality, demotions.
5. **Monthly report** (OPS-010) — sent to the owner: unattended days, verified outcomes, contradictions, interventions, cost.

## 5. Backup and recovery

| What | How | Where | Frequency |
|---|---|---|---|
| SQLite stores (C07, C06, C09, C15, C16, agent memory) | Litestream continuous replication [R] or nightly `sqlite3 .backup` + age encryption | Archive cell (home hub) + one off-site S3-compatible bucket or SFTP | Continuous / nightly |
| Home Assistant | HA full backups (farm design [V]) | Off-site via Tailscale | Daily, keep 14 |
| Registry, policy, config repos | Git (private), signed tags | GitHub + local mirror on hub | On change |
| Component keys | Not backed up (regenerated and re-enrolled after loss) — except owner keys | — | — |
| Owner root key | Hardware key + paper backup in two places | Physical | — |
| Release artifacts | GitHub releases, pinned tags | GitHub + hub cache | On release |

Every backup carries a manifest: cell, time, software version, registry and bundle hash (pattern from `edge_server` archive role [V]).

### Recovery runbooks (to be written in Stage 4, drilled quarterly)

| Scenario | Steps (outline) | Target |
|---|---|---|
| Field Station Pi dies | Flash HA OS → restore HA backup → install C10 add-on → new cell key → owner re-enrols → old key revoked | < 2 h on site |
| Home hub dies | New host → `edge_server` deploy fleet mode → restore SQLite from Litestream → re-enrol → verify audit chain continuity | < 4 h |
| VPS lost | New VPS → `my_vps` hardening → Job Service binary → restore DB → workers reconnect (outbound) | < 2 h |
| Owner ops key compromised | Root key signs rotation → all grants re-signed → revocation broadcast | < 1 h |
| Cloud LLM provider gone | Change C12 routing config; run agent eval suites against replacement | < 1 d |

## 6. Configuration management

- All configuration in git: `registry`, `policy`, per-cell `config/*.yaml`, HA packages (farm repo), placement.
- Secrets encrypted (SOPS + age) or referenced.
- Every running component reports its config hash; the console shows drift between git and running state.
- Changes flow: PR → CI validation (schemas, Cedar validate, conformance) → owner signs → cells pull/receive → readiness confirms.

## 7. Automated recovery

Adopt `edge_server`'s four layers unchanged [V]: L0 hardware (BIOS power-on, relays), L1 fixed actions (restart service, re-up Tailscale), L2 agent (A02, only when L1 fails twice), L3 owner. All L1/L2 actions go through C06 and are audited.
