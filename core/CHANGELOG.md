# Changelog — autonomy-core

Python releases are tagged `core-vX.Y.Z`, Go releases `core/go/vX.Y.Z` ([ADR-015](../design/adr/ADR-015-core-library-location-and-consumption.md)).

## 0.4.0 — unreleased (M6)

- `ApprovalService` (C05): approval requests for `require_approval` decisions; signed, attested approvals only for an allowed operator, in a private chat, with a valid code, before expiry (IT-12); requests survive restarts; `attest` for owner commands.
- `ControlState`: signed suspend / hold / resume orders, sticky on disk, local only (ST-08); `blocked(principal)`.
- Gateway: `approval_keys` for attested approvals; `blocked` checked before everything else. PDP: R2 needs an approval also when the owner asks.

## 0.3.0 — unreleased (M5)

- PDP (C04): `Pdp.decide` with grants, autonomy levels, risk classes, trust, evaluation and Cedar policies via `cedarpy` (`autonomy-core[cedar]`; fails closed without it); `load_bundle` verifies owner-signed bundles and refuses older ones; `decision_request` builds requests from proposals.
- Action Gateway (C06): `ActionGateway.execute` re-verifies decision, binding, approval and obligations; idempotency; rate limits and circuit breaker (IT-15); write-ahead journal and crash reconciliation; asynchronous adapters; signed ExecutionRecords; audit (C16) of every outcome. `MockAdapter` for tests.

## 0.2.0 — unreleased (M3)

- Registry (C03): `verify_registry`, `RegistryStore` (pinned root, last valid registry, serial), `sign_registry`, `sign_owners`, `check_document`, `fingerprint`; Go `VerifyRegistry`, `CheckRegistryDocument`, `RegistryHash`; Go CLI `verify-registry`.
- `autonomy` owner CLI (`pip install 'autonomy-core[cli]'`): `keygen`, `fingerprint`, `registry init|migrate|add-node|add-component|revoke|delegate|sign|verify|show`.
- Implements spec 0.2.0.

## 0.1.0 — unreleased (M2)

- Python `autonomy_core` and Go `autonomycore` implementing spec 0.1.0: RFC 8785 canonical JSON, Ed25519 keys in contract encoding (and edge_server's base64 form), signed objects, `params_hash`, audit hash, envelope build/verify with freshness and replay protection.
- Python: persistent `SeenStore`, `Counter`, `Outbox` (extracted from edge_server's control plane, now with fsync) and `AuditLog`.
- Go: `MemorySeen`, `VerifyChain`, `cmd/autonomy-core` for cross-language tests.
