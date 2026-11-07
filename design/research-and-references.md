# Research and References

**Status:** Draft 0.1 · 2026-11-06

## 1. Method

- **Repository inspection** (tag [V]): 26 realsystem repositories cloned at HEAD on 2026-11-06; READMEs, design docs, dependency files, code and tests reviewed; key claims checked in code (e.g. `farm_agent` tool loop, `processor` executor interface and schema, `edge_server` control plane).
- **External research** (tag [R]): official specifications and documentation where reachable, research papers, and vendor documentation. Some pages could not be fetched in this session; claims depending on them are tagged [A] instead.
- Popularity was not a selection criterion. Each technology was assessed against requirements in [technology-evaluation.md](technology-evaluation.md).

## 2. Findings by area

### Agent protocols

| Finding | Source |
|---|---|
| MCP revision 2026-07-28 is the current stable release | [MCP specification release 2026-07-28](https://github.com/modelcontextprotocol/specification/releases/tag/2026-07-28) |
| That revision removes protocol sessions and the initialize handshake, adds `server/discover`, moves long-running work to an opt-in Tasks extension, replaces server-to-client callbacks with a multi-round-trip pattern, deprecates the HTTP+SSE transport and dynamic client registration | [Summary of 2026-07-28 changes (DEV)](https://dev.to/abhijeet_singh_4577af3ef9/the-mcp-2026-07-28-spec-what-changes-and-what-breaks-4421); [Developers Digest](https://www.developersdigest.tech/blog/mcp-2026-07-28-breaking-changes) |
| A2A is hosted by the Linux Foundation (since June 2025), v1.0 released in 2026; Agent Cards at `/.well-known/agent-card.json` with skills, security schemes and JWS signatures; task states incl. `INPUT_REQUIRED`, `AUTH_REQUIRED`, `REJECTED`; JSON-RPC, gRPC and REST bindings | [Tyk: A2A architecture and specification](https://tyk.io/learning-center/a2a-protocol-architecture-and-technical-specification/) |

### Governance, evaluation and prompt injection

| Finding | Source |
|---|---|
| A panel of nine LLM judges had an effective sample size of about two independent votes; accuracy 8–22 points below independent voting; the best single judge matched or beat the panel | [Apple ML Research: Nine Judges, Two Effective Votes](https://machinelearning.apple.com/research/correlated-llm-evaluation-panels); [Correlated Errors in LLMs (arXiv 2506.07962)](https://arxiv.org/pdf/2506.07962) |
| Six design patterns with provable resistance to prompt injection: action-selector, plan-then-execute, LLM map-reduce, dual LLM, code-then-execute, context-minimization | [Beurer-Kellner et al., Design Patterns for Securing LLM Agents against Prompt Injections (arXiv 2506.08837)](https://arxiv.org/abs/2506.08837v3) |
| CaMeL: privileged LLM writes a restricted program; quarantined LLM parses untrusted data; interpreter tracks capabilities (data provenance) and enforces policies; limits: policy burden and user approval fatigue | [Debenedetti et al., Defeating Prompt Injections by Design (arXiv 2503.18813)](https://arxiv.org/pdf/2503.18813); [Simon Willison on CaMeL](https://simonwillison.net/2025/apr/11/camel/) |

### Policy engines

| Finding | Source |
|---|---|
| Rego is Datalog-derived, general purpose, CNCF, large ecosystem; Cedar is a stricter, deny-by-default, analysable, application-level authorization language (Rust, with SDKs/daemon); both compile to WASM | [Permit.io: OPA vs Cedar](https://permit.io/blog/opa-vs-cedar); [Permit.io: OPA vs Cedar vs Zanzibar](https://permit.io/blog/policy-engines); [Cedar paper (arXiv 2403.04651)](https://arxiv.org/pdf/2403.04651) |

### Durable execution and workflows

| Finding | Source |
|---|---|
| DBOS runs as a library inside the application, depends only on Postgres, MIT-licensed, SDKs for Python, TypeScript, Go, Rust, Java; Temporal is an external orchestration service with a worker fleet | [DBOS vs Temporal](https://dbos.dev/compare/compare-dbos-vs-temporal-dbos); [DBOS benchmark: external vs embedded workflows](https://dbos.dev/blog/durable-execution-coding-comparison) |
| Restate provides a Python SDK | [restate-sdk on PyPI](https://pypi.org/project/restate-sdk/) |

### Messaging and edge

| Finding | Source |
|---|---|
| NATS leaf nodes store and forward to the cloud and can be daisy-chained; JetStream mirrors/sources move data edge→cloud without connectors; nats-server is a ~15 MB single binary | [Synadia: AI at the edge with NATS JetStream](https://www.synadia.com/blog/ai-at-the-edge-with-nats-jetstream); [Synadia: intermittent connectivity case study](https://www.synadia.com/customer-stories/powerflex) |
| CloudEvents 1.0 core attributes (`id`, `source`, `specversion`, `type`, optional `subject`, `time`, `dataschema`, `datacontenttype`) and extension attributes | [CloudEvents spec v1.0.2](https://github.com/cloudevents/spec/blob/v1.0.2/cloudevents/spec.md) |

### Storage and backup

| Finding | Source |
|---|---|
| Litestream continuously replicates SQLite to S3-compatible storage, GCS, Azure, SFTP, local paths, NATS JetStream and WebDAV; `restore` rebuilds the database | [Litestream: Getting started](https://litestream.io/getting-started/) |
| SQLite and Postgres vector extensions (sqlite-vec, pgvector) for retrieval inside the existing database | [RAG inside an RDBMS with sqlite-vec and pgvector (DEV)](https://dev.to/jonbiz/implementing-a-rag-system-inside-an-rdbms-sqlite-and-postgres-with-sqlite-vec-pgvector-4d5h) |

### Observability

| Finding | Source |
|---|---|
| OpenTelemetry GenAI semantic conventions have moved to a dedicated repository; client, agent and MCP conventions are maintained there | [OTel GenAI agent spans page (moved notice)](https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-agent-spans); [semantic-conventions-genai](https://github.com/open-telemetry/semantic-conventions-genai) |

### Home Assistant

| Finding | Source |
|---|---|
| WebSocket API: `auth_required` → `auth` (long-lived token) → `auth_ok`; `subscribe_events`, `get_states`, `call_service` (with `return_response` since 2024.4) | [HA WebSocket API reference (mirror)](https://www.mintlify.com/home-assistant/core/api/websocket-api) |

### Identity, network, secrets, supply chain

| Finding | Source |
|---|---|
| Headscale is an open-source, self-hosted implementation of the Tailscale control server with ACL support | [Headscale ACLs](https://headscale.net/stable/ref/acls/) |
| Vault vs SOPS trade-offs for small deployments | [Vault vs SOPS](https://devopsninja-tech.pages.dev/security/vault-vs-sops/); [Infisical: Vault alternatives](https://infisical.com/blog/hashicorp-vault-alternatives) |
| SLSA provenance and Sigstore signing with GitHub artifact attestations | [GitHub blog: SLSA 3 with GitHub Actions and Sigstore](https://github.blog/2022-04-07-slsa-3-compliance-with-github-actions/); [Verified builds guide](https://guptadeepak.com/guides/verified-builds-slsa-sigstore/) |

### Models

| Finding | Source |
|---|---|
| LiteLLM offers a self-hosted OpenAI-compatible proxy across many providers; Ollama exposes an OpenAI-compatible API for local models | [LiteLLM proxy guide](https://bestllmfor.com/guide/litellm-proxy-gateway-local/); [LiteLLM vs Ollama](https://toolhalla.ai/compare/litellm-vs-ollama) |

## 3. Facts verified from repositories (selection)

| Claim | Where |
|---|---|
| `processor` job schema with leases, attempts, priority; `Executor.Execute(ctx, jobType, map)` | `processor/control-plane/internal/db/migrations/001_initial_schema.sql`, `data-plane/internal/executor/executor.go` |
| `edge_server` Ed25519 signing, canonical JSON, `SeenStore`, `Counter`, `Outbox` | `edge_server/telegram-bot/controlplane.py` |
| Fleet design: tiers T0–T3, approvals with TOTP, incident agent limits, power stages | `edge_server/docs/design/edge-system.md` |
| `jarvis` `LLMProvider`, `Orchestrator`, `Tool` ports; Pipecat confined to `voice/` | `jarvis/docs/architecture.md`, ADR-0002 |
| `farm_agent` system prompt unused; tool results in wrong format; hard-coded allowlist | `farm_agent/farm_agent/agent.py` lines 54–95, 171, 233–240 |
| `knowledge` is a YouTube Telegram bot | `knowledge/README.md`, `bot/` |

## 4. What could not be verified

- MCP 2026-07-28 changelog on modelcontextprotocol.io (blocked); secondary summaries used.
- DBOS SQLite support (U-03).
- Whether Home Assistant tokens can be scoped (R-06, U-02).
- Cedar license and binding maturity (R-04).
