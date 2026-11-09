# autonomy-core v0.1.0 (C02)

**Status:** v0.1.0 draft (milestone M2) · 2026-11-08 · implements [spec](../spec/README.md) 0.1.0 · location and releases: [ADR-015](../design/adr/ADR-015-core-library-location-and-consumption.md)

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
