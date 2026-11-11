# autonomy-core v0.5.0 (C02; C03 tools; C04 PDP; C05 approvals; C06 gateway; C17, C18 conventions)

**Status:** v0.3.0 draft (milestones M2, M3, M5, M6, M9) · 2026-11-10 · implements [spec](../spec/README.md) 0.4.0 · location and releases: [ADR-015](../design/adr/ADR-015-core-library-location-and-consumption.md)

The one implementation of what every component does at its boundary: canonical JSON, Ed25519 keys and signatures, building and accepting envelopes, replay protection, a restart-safe sequence counter, a persistent outbox and the hash-chained audit log. Extracted from `edge_server`'s control plane.

| | Python `core/python/` | Go `core/go/` |
|---|---|---|
| Install | `pip install "autonomy-core @ https://github.com/realsystem/autonomy/archive/<ref>.tar.gz#subdirectory=core/python"` | `go get github.com/realsystem/autonomy/core/go@<ref>` |
| Import | `import autonomy_core as ac` | `core "github.com/realsystem/autonomy/core/go"` |
| Dependencies | `cryptography` | standard library |
| RFC 8785 canonical JSON | `canonicalize` | `Canonicalize`, `Decode` |
| Keys | `generate_private_key`, `load_private_key`, `save_private_key`, `encode_public_key`, `decode_public_key`, `KeyRing` | `GenerateKey`, `EncodePublicKey`, `DecodePublicKey`, `KeyRing` |
| Signed objects | `sign_object`, `verify_object`, `params_hash`, `audit_hash` | `SignObject`, `VerifyObject`, `ParamsHash`, `AuditHash` |
| Envelopes | `make_envelope`, `verify_envelope`, `Freshness` | `NewEnvelope`, `VerifyEnvelope`, `Freshness` |
| Replay / sequence | `SeenStore` (on disk), `Counter` (on disk) | `Seen` interface, `MemorySeen` |
| Outbox | `Outbox` (on disk) | — (M4, for `processor`) |
| Audit | `AuditLog` (append, fsync), `verify_chain` | `VerifyChain` |
| Registry | `verify_registry`, `RegistryStore`, `sign_registry`, `sign_owners`, `fingerprint` | `VerifyRegistry`, `CheckRegistryDocument`, `RegistryHash` |
| Evaluation (C13) | `Evaluator`, `value_range`, `allowed_targets`, `Review` | — |
| Policy decisions (C04) | `Pdp`, `PolicyBundle`, `load_bundle`, `decision_request`; Cedar via `cedarpy` | — |
| Approvals and owner control (C05) | `ApprovalService`, `ControlState` | — |
| Action Gateway (C06) | `ActionGateway`, `AdapterResult`, `MockAdapter` | — |
| Verification (C14) | `Verifier`, `Probe`, `Observation`, `undo_proposal` | — |
| Logs, traces, redaction (C17) | `configure_logging`, `Tracer`, `REDACTOR`, `correlation`, `reconstruct` | — |
| Secrets (C18) | `resolve`, `resolve_config`, `register_provider`, `scan` | — |
| Ids and time | `new_ulid`, `prefixed_id`, `new_traceparent`, `to_rfc3339`, `parse_rfc3339` | `NewULID`, `NewTraceparent`, `FormatTime`, `ParseTime` |

## Use

```python
import autonomy_core as ac

key = ac.load_private_key("/data/keys/node.pem")
seq = ac.Counter("/data/state/seq", start=time.time())
env = ac.make_envelope(key, source="cell:field-station", type="health.heartbeat",
                       data={"uptime": 3600}, seq=seq.next(), ttl=180)

keys = ac.KeyRing({"cell:home-hub": "ed25519:..."})
seen = ac.SeenStore("/data/state/seen.json")
try:
    sender = ac.verify_envelope(env, keys, seen=seen, freshness=ac.Freshness(max_lifetime=600))
except ac.Reject as why:
    log.warning("refused: %s", why.reason)
```

`verify_envelope` checks, in order: structure, signature, freshness, replay — so unsigned traffic cannot fill the replay store. `Reject.reason` is a short stable phrase, identical in Python and Go: `malformed`, `unknown protocol version`, `unknown signer`, `bad signature`, `from the future`, `expired`, `lifetime too long`, `replayed`, `too many messages in flight`.

Freshness defaults: up to 120 s in the future; no lifetime cap; envelopes without `expires` accepted for 600 s after `time`. Receivers of commands should set `require_expires`.

## The owner's registry tool

`pip install "autonomy-core[cli] @ https://github.com/realsystem/autonomy/archive/<ref>.tar.gz#subdirectory=core/python"` gives the `autonomy` command:

```bash
autonomy keygen owner-root.pem                      # offline: signs only the owners section
autonomy keygen owner-ops.pem                       # online: signs day-to-day registry updates
autonomy registry migrate fleet.yaml registry.yaml --root-key owner-root.pem --ops-key owner-ops.pub
autonomy registry add-component registry.yaml --manifest autonomy.manifest.json --key ed25519:... --cells home-hub
autonomy registry sign registry.yaml --key owner-ops.pem     # registry.json + registry.sig.json, serial + 1
autonomy registry verify registry.json --root owner-root.pub
autonomy registry revoke registry.yaml svc:old --reason "key lost"
autonomy registry delegate registry.yaml --root-key owner-root.pem --add new-ops.pub --remove owner-ops.pub
```

Edits rewrite the YAML without its comments. `sign` refuses to write a signature cells would reject (inconsistent document, older serial). A cell uses `RegistryStore(state_dir).accept(doc, sig)`: it pins the root on first use and keeps the last valid registry when an update is refused. Rotating the root key means re-pinning every cell (delete `owner-root.pub` in its registry state) — deliberately manual.

## Policy decisions and the gateway

```python
pdp = ac.Pdp(issuer="svc:pdp@field-station", key=key, bundle=ac.load_bundle(bundle_doc, registry))
decision = pdp.decide(ac.decision_request(proposal, cell="field-station", risk_class="R1", evaluation="ok"))
gateway = ac.ActionGateway(executor="svc:action-gateway@field-station", key=key, state_dir="/state/gw",
                           pdp_keys=ac.KeyRing({"svc:pdp": pdp_public}), registry=registry,
                           adapters=[ha_adapter], audit=ac.AuditLog("/state/audit.jsonl", cell="field-station",
                           actor="svc:action-gateway@field-station", key=key))
gateway.reconcile()   # at start-up: settle actions interrupted by a crash
record = gateway.execute({"action_id": ..., "proposal": proposal, "decision": decision,
                          "approval": approval, "idempotency_key": proposal["idempotency_key"]})
```

Decision rules (R3 never; Cedar `forbid` wins; grant, policy, owner or R0 must permit; untrusted input, evaluator escalation or a level below the risk send it to the owner; `@approvable` policies give `require_approval`) are in the `pdp` module docstring; gateway checks in the `gateway` module docstring. Real Cedar is tested in CI (`policy engine (Cedar)` job).

## Evaluate before, verify after

```python
evaluator = ac.Evaluator(invariants={"ha.helper.set": {"value_range": ac.value_range("value", 50, 150)}},
                         open_proposals=lambda: in_flight, probes={"ha.state"}, reviewer=None, audit=audit)
evaluation = evaluator.evaluate(proposal, risk_class="R1")          # ok | escalate | reject
decision = pdp.decide(ac.decision_request(proposal, ..., evaluation=evaluation["outcome"]))
record = gateway.execute(...)                                         # succeeded = adapter accepted

verifier = ac.Verifier(verifier="svc:verifier@field-station", key=key, state_dir="/state/ver", audit=audit,
                       probes={"ha.state": ac.Probe(read_ha_state, independent=False)},
                       on_contradicted=lambda verification, undo: notify_owner(verification, undo))
verifier.check_pending(gateway)   # call periodically: confirmed | contradicted | not_confirmed | inconclusive
```

Check order and outcomes are in the `evaluator` module docstring; results in the `verifier` docstring. A reviewer (`Review(verdict, note, model)`, an LLM hook) only ever escalates. `autonomy evaluate proposal.json --risk R1` and `autonomy verify proposal.json record.json --observations obs.json` run both over files; `core/python/tests/scenarios/` has recorded scenarios (ST-12: accepted zone run, no flow → `contradicted`).

## Logs, traces and secrets

- Logs: `configure_logging("svc:x", "svc:x@cell")` — JSON lines matching spec `log-record`, with `trace_id`, `span_id` and `correlationid` from the current context; `AUTONOMY_LOG_FORMAT=text` keeps text. Everything passes the redactor.
- Traces: `Tracer("/state/traces.jsonl", service=...)` writes OTLP/JSON span lines, offline; an OpenTelemetry Collector on the hub can read them later (`filelog`/`otlpjson` receivers). Continue an envelope's trace with `tracer.span(name, envelope["traceparent"])`. Audit records written inside a span carry its `trace_id`, so `autonomy trace <correlationid> --audit audit.jsonl --traces traces.jsonl` rebuilds an action story (IT-71).
- Secrets: configs hold `secret://env/NAME`, `secret://file/path`, `secret://sops/file.yaml#dotted.key`, `secret://age/file.age` or `secret://store/name` (host provider); `resolve_config` replaces them and registers each value with the redactor.
- Scanning: `autonomy secrets scan . --allowlist .secrets-allowlist` (CI: `secrets.yml`). Allowlist lines are `<glob> [rule]`; a line containing `secret-scan: allow` is skipped.

## Tests

- Both languages run the spec's canonical-JSON, hash and signature vectors and verify every signed fixture; Go reproduces the Python signatures byte for byte.
- `core/python/tests/test_cross_go.py` builds the Go CLI (`cmd/autonomy-core`) and checks Python → Go and Go → Python envelopes, tampering, and 300 random JSON values canonicalized identically.
- Python output is validated against the spec schemas with the conformance kit.

```bash
cd core/python && python -m pytest && ruff check src tests && mypy src
cd core/go && go vet ./... && go test -race ./...
```

CI: [`.github/workflows/core.yml`](../.github/workflows/core.yml).

## Known differences

- Go's `encoding/json` replaces lone UTF-16 surrogates with U+FFFD when decoding; Python refuses to canonicalize them. Neither produces them.
- The Go module has no on-disk replay store, counter or outbox yet; `processor` (M4) will need them.
