# Risks and Open Questions

**Status:** Draft 0.1 · 2026-11-06

## 1. Risks

| # | Risk | Likelihood | Impact | Mitigation | Owner decision needed? |
|---|---|---|---|---|---|
| R-01 | **Scope** — a one-person programme designing 21 components | High | High | Foundation is three small pieces; every stage ends with something used; most components are libraries or tables, not services | No |
| R-02 | **Approval fatigue** — owner approves without reading | High | High | Daily batches, ≤ 5-line diffs, promotion to A3 for proven R1 work, fast-approve metrics | No |
| R-03 | **Prompt injection through approved paths** | Medium | High | Taint propagation, quarantined readers, GOV-007; owner sees "derived from untrusted source" on approval | No |
| R-04 | **Cedar Python binding maturity** [A] | Medium | Medium | Wrap behind C02 `Pdp` interface; fallback to Go PDP via local HTTP or to OPA | No |
| R-05 | **Revocation latency for offline cells** | Medium | Medium | Short grant expiry per risk class; Telegram out-of-band revocation | No |
| R-06 | **HA token scope** — HA access tokens are not limited to entities [A] | High | Medium | Adapter-enforced allowlists; separate read/write paths; audit sampling | No |
| R-07 | **Pi 5 resources** with HA + Frigate-like loads | Medium | Medium | Keep inference off the Pi; measure in M10 (NFR-006) | No |
| R-08 | **Clock skew offline** | Low | Medium | Sequence numbers, skew tolerance, RTC | No |
| R-09 | **Refactoring `edge_server` while it is in active use** | Medium | Medium | Extract C02 behind its existing tests (~800); classic mode untouched [V] | No |
| R-10 | **`processor` has no CI** | High | Medium | CI is the first change (J9) | No |
| R-11 | **Coqui TTS upstream maintenance** [A] | Medium | Low | Executor interface allows swapping engine | No |
| R-12 | **Tailscale hosted control plane dependency** | Low | Medium | Headscale option; LAN fallback for same-site | Yes (Q-05) |
| R-13 | **GenAI telemetry conventions still changing** [R] | High | Low | Pin version | No |
| R-14 | **Design and contracts in one repo** — design churn could obscure contract releases | Low | Low | Contracts in `spec/` with their own version tags; split into a separate repo only if needed (ADR-014) | No |
| R-15 | **Cost of cloud LLMs** for daily agents | Medium | Low | Budgets in C12; local models for routine assessments | No |

## 2. Open questions for the owner

| # | Question | Default if unanswered | Needed by |
|---|---|---|---|
| Q-01 | ~~Dedicated repository for the design~~ — resolved: `realsystem/autonomy` created 2026-11-06 | — | — |
| Q-02 | Rename the `knowledge` repository? | Rename to `youtube-answers` | M19 |
| Q-03 | Which machine is the `home-hub` cell (always-on server vs PC)? | Assume always-on low-power server | M6 |
| Q-04 | Owner root key custody: hardware key (YubiKey/SoloKey) or paper + encrypted file? | Hardware key + paper backup | M3 |
| Q-05 | Stay on hosted Tailscale or self-host Headscale? | Hosted Tailscale; revisit at M14 | M14 |
| Q-06 | Which cloud model providers are acceptable for `site`-privacy farm data? | Allowed for Q&A; assessments local by default | M7 |
| Q-07 | Monthly budget for cloud LLM usage across agents | Set a conservative cap in C12 | M7 |
| Q-08 | ~~Should `jarvis` remain on GCP?~~ | Closed 2026-11-11: `jarvis` is out of scope (ADR-017) | — |
| Q-09 | Archive list confirmation (`thequeue`, `fuel_economy`, `hw`, pre-2025 repos)? | Archive after confirmation | Any time |
| Q-10 | Are any `airshipit` repos intended to be part of this system? | No | — |
| Q-11 | Approval channel beyond Telegram: web console with passkeys required at M6, or later? | Telegram first; console at M6 for viewing, approvals later | M6 |

## 3. Unresolved technical questions

| # | Question | How to resolve |
|---|---|---|
| U-01 | Exact OpenSprinkler and RPS behaviour (inherits farm open questions [V]) | Farm Phase 1 |
| U-02 | Does HA permit a non-admin user with service-call restrictions sufficient for the write path? | Test in HA dev container at M10 |
| U-03 | DBOS SQLite support for a possible Pi-hosted workflow engine | Check docs when ADR-006 triggers (could not be confirmed during research) |
| U-04 | Vector extension choice for C15 (sqlite-vec vs alternatives) | Spike at M19 |
| U-05 | Telegram bot-to-bot reliability as a backup machine path | `edge_server` already treats it as optional [V]; measure |
| U-06 | Whether `edge_server`'s ha-addon packaging can host both the cell and the adapter with separate credentials | Prototype at M10 |
