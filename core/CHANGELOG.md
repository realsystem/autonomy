# Changelog — autonomy-core

Python releases are tagged `core-vX.Y.Z`, Go releases `core/go/vX.Y.Z` ([ADR-015](../design/adr/ADR-015-core-library-location-and-consumption.md)).

## 0.7.0 — unreleased (M7)

- **Model gateway (C12, ADR-017):** `ModelGateway.complete(request)` takes a task class and the data's privacy, never a provider.
  - Endpoints declare a locality (`cell`, `site`, `owned`, `cloud`). Privacy decides which localities may see the data, checked before connecting and again by a per-endpoint host allowlist (IT-83).
  - Routes are tried in order, with a circuit breaker per endpoint. `ModelUnavailable` carries a code: `unavailable.model`, `.timeout`, `.invalid_output`, `policy.model_refused` or `budget.exceeded`.
  - Per-component daily budgets for tokens and calls survive restarts through the usage log. The log holds model-usage records with no prompt text.
  - Structured output: an `output_schema` answer is validated and retried once.
  - Untrusted content is fenced as data.
  - Adapters use no SDKs: `processor` (an `llm.chat` job; sensitive data as a `sensitive` job with a short retention; idempotent per request and endpoint; long-poll; cancel on deadline or stop), `openai` (any OpenAI-compatible server), `anthropic`, and `FakeProvider` for tests.
- **Agent host (C11):** `AgentHost.register(manifest, run)` and `run(agent, trigger)`.
  - Tools are bound from `manifest.agent.tools`, and their results stay data, with trust.
  - `ctx.ask` uses the agent's privacy and budgets.
  - `ctx.propose` only allows action types from `proposes_actions`, nothing for quarantined agents, and at most `proposals_per_day`; proposals go to `submit`.
  - Suspension or the run's time budget cancels the in-flight model call (the processor job too) and withdraws the agent's pending proposals.
  - The audit log gets `agent.started`, `agent.finished`, `agent.suspended` and `agent.budget_exceeded`.
- **`ModelReviewer`:** the M8 reviewer hook on the gateway (task class `review`, private, JSON verdict). With no model, `ReviewUnavailable` makes the evaluator record the review as skipped without blocking (ST-03). An unusable answer still escalates.
- **CLI:** `autonomy models check | ask | usage`. Example config: `ops/models.example.yaml`.
- `SPEC_VERSION` is 0.6.0 (model-request, model-usage, `manifest.agent`).

## 0.6.0 — unreleased (M8)

- Evaluator (C13): `Evaluator.evaluate(proposal, risk_class=)` returns a spec Evaluation. It runs these deterministic checks: schema, input freshness, trust, invariants per action type (`value_range`, `allowed_targets`, or your own), conflicts with open proposals, whether the effect is verifiable, and reversibility. Stale or future inputs are rejected and never `ok`. An optional reviewer (an LLM hook) runs only when nothing was rejected, and can only turn `ok` into `escalate`: errors and unknown answers escalate too. Each evaluation is audited as `action.evaluated`.
- Verifier (C14): `Verifier.check(proposal, record)` reads the declared probe and returns a signed Verification (`confirmed`, `contradicted`, `not_confirmed` or `inconclusive`), or `None` while the window is still open. Effect kinds are `state_equals`, `state_within` and `restarted_after`. Observations from before the action started are ignored. `independent` comes from the verifier's knowledge of the probe, not from the proposer. A `contradicted` result also writes `incident.opened` and builds an undo proposal (`undo_proposal`) for `on_contradicted` (ST-12). Results persist in `verifications.jsonl`.
- Gateway: keeps each action's expectations in its journal, and `awaiting_verification()` lists succeeded actions that are still unverified, across restarts. `Verifier.check_pending(gateway)` checks all of them.
- CLI: `autonomy evaluate` (exit 0 ok, 1 escalate, 2 reject) and `autonomy verify` (against recorded observations).
- Recorded scenarios in `core/python/tests/scenarios/`, including ST-12 no-flow.
- `SPEC_VERSION` is 0.5.0, as M9 left it.

## 0.5.0 — unreleased (M9)

- Observability (C17): `configure_logging` (spec log-record JSON lines, trace and correlation ids from context), `Redactor` (registered values, secret-shaped text, secret field names), `Tracer` (OTLP/JSON span lines, `traceparent` propagation, offline), `reconstruct` (IT-71). Gateway and PDP emit spans; audit records carry `trace_id`; `gateway.execute(..., traceparent=, correlationid=)`.
- Secrets (C18): `secret://env|file|sops|age|store/...` references, `resolve_config`, provider hook; resolved values are redacted everywhere. Scanner `scan` / `autonomy secrets scan` with an allowlist (IT-81). `autonomy trace` reconstructs a story.

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
