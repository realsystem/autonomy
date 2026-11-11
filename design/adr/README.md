# Architecture Decision Records

| ADR | Title | Status |
|---|---|---|
| [ADR-001](ADR-001-cells-with-local-enforcement.md) | Federation of cells with local enforcement | Accepted |
| [ADR-002](ADR-002-contracts-first-cloudevents-json-schema.md) | Contracts first: JSON Schema + CloudEvents envelope | Accepted |
| [ADR-003](ADR-003-ed25519-identity-owner-signed-registry.md) | Ed25519 identities and an owner-signed registry | Accepted |
| [ADR-004](ADR-004-cedar-embedded-pdp.md) | Cedar as policy language with an embedded PDP | Provisional |
| [ADR-005](ADR-005-action-gateway-sole-executor.md) | The Action Gateway is the sole executor of consequential actions | Accepted |
| [ADR-006](ADR-006-extend-processor-defer-workflow-engine.md) | Extend `processor` for jobs; defer a workflow engine | Provisional |
| [ADR-007](ADR-007-signed-http-outbox-now-nats-later.md) | Signed HTTP + SQLite outbox now; NATS JetStream later | Provisional |
| [ADR-008](ADR-008-llm-review-only-escalates.md) | LLM review can only escalate; no LLM-vote authorization | Accepted |
| [ADR-009](ADR-009-mcp-behind-gateway-a2a-deferred.md) | MCP for tool exposure behind the gateway; A2A deferred | Accepted |
| [ADR-010](ADR-010-sqlite-first-separate-stores.md) | SQLite first, separate stores by data kind | Accepted |
| [ADR-011](ADR-011-python-go-no-kubernetes.md) | Python and Go; containers and systemd; no Kubernetes | Accepted |
| [ADR-012](ADR-012-model-gateway-library.md) | Model Gateway as a library from `jarvis`; no agent framework dependency | Accepted; to be superseded by ADR-017 |
| [ADR-013](ADR-013-sops-age-and-cell-secret-stores.md) | Secrets: per-cell stores + SOPS/age; no Vault now | Accepted |
| [ADR-014](ADR-014-design-package-location.md) | Design package and contracts live in `realsystem/autonomy` | Accepted |
| [ADR-015](ADR-015-core-library-location-and-consumption.md) | Core library (C02) lives in `realsystem/autonomy/core/`, consumed by pinned ref | Proposed |
| [ADR-016](ADR-016-registry-signing-and-trust-anchor.md) | Registry: detached signature with serial, root-signed owners, root pinned on first use | Proposed |
| [ADR-017](ADR-017-model-gateway-and-agent-host-in-core.md) | Model Gateway and Agent Host built in `autonomy-core`; `jarvis` out of scope | Proposed |

Provisional decisions are expected to change when their revisit condition is met.
