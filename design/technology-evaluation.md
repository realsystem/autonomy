# Technology Evaluation

**Status:** Draft 0.1 · 2026-11-06 · Sources: [research-and-references.md](research-and-references.md)

Rule: existing repository technologies first; add infrastructure only against a measured requirement (brief §10). Verdicts: **Adopt**, **Adapt** (use with changes/wrapping), **Build** (small, our own), **Defer** (revisit condition given), **Reject**.

## 1. Summary

| Area | Choice | Verdict | Alternatives considered | Ops cost | Lock-in | ADR |
|---|---|---|---|---|---|---|
| Languages | Python (cells, agents, adapters, gateway) + Go (Job Service, workers) | Adopt | Rewrite all in one language; Rust | None new | None | ADR-011 |
| Packaging | Docker/compose + systemd on hosts; HA add-ons on HA OS | Adopt | Kubernetes, k3s, Nomad | Low | None | ADR-011 |
| Orchestration | None (cells + deploy scripts) | Adopt | k3s, Nomad | — | — | ADR-011 |
| Contracts | JSON Schema 2020-12 + CloudEvents 1.0 envelope | Adopt | Protobuf/gRPC, AsyncAPI-only | Low | None (open standards) | ADR-002 |
| Identity | Ed25519 keys + owner-signed registry (from `edge_server`) | Adapt | SPIFFE/SPIRE, OAuth/OIDC IdP, mTLS PKI | Low | None | ADR-003 |
| Network | Tailscale; Headscale optional | Adopt (existing) | WireGuard manual (`my_vps`), ZeroTier, Nebula | Low | Medium (hosted control plane) → Headscale exit | — |
| Policy | Cedar embedded (`cedar-go`, `cedarpy`) | Adopt (provisional) | OPA/Rego, OpenFGA, code | Low | Low (open source, Apache-2.0 [A]) | ADR-004 |
| Jobs | `processor` (Go, SQLite) | Adapt | Celery, RQ, River, Temporal | Low | None | ADR-006 |
| Workflows | Tables + coordinator in Job Service | Build (small) → Defer engine | DBOS, Temporal, Restate, Prefect | Low | None | ADR-006 |
| Messaging | Signed HTTP + SQLite outbox (from `edge_server`) | Adapt → Defer broker | NATS JetStream, MQTT (Mosquitto), Redis Streams, Kafka | Low | None | ADR-007 |
| Storage | SQLite (WAL) per component; Postgres where apps already use it | Adopt | Postgres everywhere, DuckDB | Low | None | ADR-010 |
| Vector search | SQLite vector extension in C15; pgvector later | Defer (M19) | Qdrant, Chroma, Weaviate | — | — | ADR-010 |
| Backup | Litestream / `sqlite3 .backup` + age | Adopt | restic, borg (also fine for files) | Low | None | — |
| Secrets | Per-cell store + SOPS/age | Adopt | Vault/OpenBao, Infisical, cloud SM | Low | None | ADR-013 |
| Observability | OpenTelemetry SDK + Collector; Prometheus/Grafana; Jaeger/Tempo | Adopt | Vendor APM, ELK | Medium (hub only) | Low (OTLP) | — |
| Model access | C12 library in `autonomy-core`: OpenAI-compatible + Anthropic HTTP adapters, no SDKs; Ollama for local | Build (ADR-017) | LiteLLM proxy, OpenRouter, direct SDKs | Low | Low | ADR-017 (was ADR-012) |
| Local models | Ollama (used in 4 repos [V]); llama.cpp server as alternative | Adopt | vLLM (GPU servers), LM Studio | Low–Medium | Low | ADR-017 (was ADR-012) |
| Agent framework | None as platform dependency; thin agent loop in C11 | Build (thin) | OpenAI Agents SDK, LangGraph, CrewAI, AutoGen, Pydantic AI | Low | Avoided | ADR-017 (was ADR-012) |
| Tool protocol | MCP (2026-07-28) behind C06 | Adopt (optional) | Custom JSON tools only | Low | Low (open spec) | ADR-009 |
| Agent interop | A2A v1.0 | Defer | — | — | — | ADR-009 |
| Supply chain | Pinned deps, CI-built signed tags, GitHub artifact attestations / Sigstore | Adopt | — | Low | Low | — |

## 2. Key evaluations

### 2.1 Workflow / durable execution

| | `processor` + small workflow tables | DBOS | Temporal | Restate |
|---|---|---|---|---|
| Problem solved | Durable jobs; simple sagas | Durable functions as a library, checkpoints in Postgres [R] | Durable workflows, mature, polyglot | Durable handlers via a single server binary [A] |
| Footprint | Existing Go binary + SQLite | Library + Postgres | Server cluster + DB (Cassandra/Postgres/MySQL) | One server |
| Fit for 1 GB VPS / Pi | Yes | Needs Postgres | No | Possible |
| Language fit | Go now; Python via worker API | Python, TS, Go, Java, Rust [R] | Many SDKs | Python, TS, Java, Go, Rust [A] |
| Lock-in | None | Low (MIT) | Low–medium | Low–medium |
| Verdict | **Now** | **First candidate** when ADR-006 conditions trigger | Only if scale/complexity grows a lot | Watch |

### 2.2 Messaging

| | Signed HTTP + outbox | NATS JetStream | MQTT (Mosquitto) |
|---|---|---|---|
| Already in repos | Yes (`edge_server` [V]) | No | Yes (optional in `edge_server` [V]) |
| Offline edge | Outbox retries | Leaf nodes store-and-forward; ~15 MB single binary [R] | Broker-local only; no durable cross-site replay without extra work |
| Fan-out subscriptions | Manual | Native | Native |
| Ops cost | None | One more service per cell | One more service |
| Verdict | **Now** | **Revisit** when: > 10 cells, or > 5 consumers per topic, or sustained > 50 msg/s, or a second team | Keep for device-level telemetry only (Frigate, sensors) |

### 2.3 Policy

See [governance-and-authority.md §4](governance-and-authority.md#4-policy-engine). Cedar is chosen for analysability and application-level fit [R]; OPA remains a fallback if Python bindings prove unreliable [A].

### 2.4 Identity

SPIFFE/SPIRE solves workload identity at datacenter scale; it adds a server and agents per node. With < 20 cells and one owner, owner-signed Ed25519 registry entries (already implemented for nodes [V]) give the same properties — verifiable identity, rotation, revocation — without new infrastructure. Revisit if Autonomy ever runs on Kubernetes or across organisations.

### 2.5 Model gateway: library vs proxy

| | C12 library (Python) | LiteLLM proxy | Direct SDKs |
|---|---|---|---|
| Provider independence | Own port + two wire adapters (ADR-017) | 100+ providers via OpenAI-compatible API [R] | None |
| Privacy routing (`local_only`) | Enforced in-process before any network call | Config-based routing; data reaches the proxy | Per call site |
| Dependency surface | Small | Large Python dependency tree (supply-chain exposure) [A] | Per SDK |
| Ops cost | None | One more service | None |
| Verdict | **Adopt** | Optional behind C12 if many providers are needed | Reject for new code |

### 2.6 Agent frameworks

Agent frameworks change quickly and each brings its own state, tool and memory model. Autonomy's core contracts (proposal, decision, execution) must not depend on one. Agents may use any framework internally if they emit the contracts; C11 provides only a thin loop (plan-then-execute, budgets, tool binding). `farm_agent` already shows the risk: its README claims one SDK while the code uses another [V].

## 3. Resource estimates (reference deployment)

| Unit | RAM | CPU | Disk |
|---|---|---|---|
| `autonomy-cell` on Pi 5 (C10 + C04 + C06 + C09 + C16 + HA adapter) | ≤ 300 MB | < 5 % avg | < 2 GB/yr events (sensor events sampled) [A] |
| A01 agent (cloud model) | 150–250 MB | low | small |
| A01 with local model on Pi | Not recommended; route local inference to `gpu-pc`/`home-hub` | — | — |
| Job Service control plane | ~50–100 MB (Go, SQLite) on 1 GB VPS [V: designed for it] | low | per job volume |
| Hub observability (Prometheus + Grafana + Tempo/Jaeger) | 1–2 GB | low | 10–30 GB |
