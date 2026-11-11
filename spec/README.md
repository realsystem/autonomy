# Autonomy contracts — `spec` v0.5.0

**Status:** v0.5.0 draft (M1 contracts, M3 registry, M5 policy bundle, M6 approvals and control orders, M9 audit trace ids) · 2026-11-08 · Source of truth for the contracts described in [design/component-contracts.md](../design/component-contracts.md). Where the two disagree, this folder wins and the design doc is corrected.

A component joins Autonomy by passing these schemas and rules in its own CI — no core component is modified.

## Contents

| Path | What |
|---|---|
| [`schemas/v0/`](schemas/v0/) | JSON Schema 2020-12, one file per contract object, `$id` `https://autonomy.local/spec/v0/<name>.schema.json` |
| [`autonomy_conformance/`](autonomy_conformance/) | The `autonomy-conformance` CLI (Python ≥ 3.10): schema validation, rules JSON Schema cannot express, signature checks, compatibility checker |
| [`fixtures/v0/<schema>/valid/`](fixtures/v0/) | Documents that must pass. Signed with the test keys |
| [`fixtures/v0/<schema>/invalid/`](fixtures/v0/) | `{"description", "expect", "instance"}` — `instance` must fail, with an error containing `expect` |
| [`fixtures/keys/test-keys.json`](fixtures/keys/test-keys.json) | **Public test keys** (seeds are published). Never trust them outside tests |
| [`vectors/`](vectors/) | Canonical JSON, hash and signature vectors for other implementations (C02 Python and Go) |
| [`examples/`](examples/) | Draft manifests and samples for C07 Job Service (`processor`, L2) and C10 Cell Runtime (`edge_server`, L0); the real ones belong in those repositories |
| [`tools/gen_fixtures.py`](tools/gen_fixtures.py) | Regenerates `fixtures/`, `vectors/` and `examples/` deterministically; `--check` fails on stale files |

### Schemas

| Contract (manifest name) | Schemas | Design § |
|---|---|---|
| — | `common` (shared definitions), `manifest` | §1, §2 |
| `envelope` | `envelope` | §3 |
| `health` | `health` (liveness and `health.reported` data), `ready`, `cell-status` | §4 |
| `error` | `error` | §7 |
| `logging` | `log-record` | §6 |
| `action-lifecycle` | `grant`, `policy-bundle`, `action-proposal`, `evaluation`, `decision-request`, `decision`, `approval`, `control-order`, `action-request`, `execution-record`, `verification` | §8 |
| `audit` | `audit-record` | §9 |
| `job` | `job` | §10 |
| `registry` | `registry`, `registry-signature` | C03, [distributed-runtime §2](../design/distributed-runtime.md#2-node-identity-and-enrollment) |

## Use it in a component's CI

```bash
pip install "autonomy-conformance @ git+https://github.com/realsystem/autonomy@spec-v0.1.0#subdirectory=spec"

autonomy-conformance check --manifest autonomy.manifest.json --samples conformance/samples --level L1 \
    --keys conformance/test-keys.json --strict
```

`--level` checks that the manifest declares the contracts for that level: **L0** `envelope`, `health` · **L1** + `action-lifecycle`, `audit` · **L2** `envelope`, `health`, `job` (design §0). `--keys` enables signature checks (a JSON object `{component_ref: "ed25519:…"}`, or `{"public": {...}}`). `--strict` fails on warnings.

**Samples** are `.json` (one document) or `.jsonl` (one per line). The schema for each is taken from, in order: the parent directory name (`samples/grant/x.json`), the file name (`x.grant.json`), or the presence of `specversion` (envelope). Envelope samples must be sent by the manifest's component; `data` is validated against `dataschema` when it is a spec URI, and against the expected schema for known types (`action.proposed` → `action-proposal`, `job.*` → `job`, …). Consecutive audit records in one `.jsonl` file are checked as a chain.

Other commands: `validate <schema> FILE…`, `selftest`, `compat --old DIR --new DIR`, `canonicalize FILE [--without signature]`, `params-hash FILE`, `schemas`.

## Rules

### Identifiers

| Item | Form | Example |
|---|---|---|
| `component_id` | `<kind>:<name>`, kind ∈ `cell svc agent adapter app`; name `[a-z0-9]+(-[a-z0-9]+)*` | `adapter:ha` |
| Instance (`component_ref`) | `<component_id>@<cell>` | `adapter:ha@field-station` |
| Owner | `owner:root`, `owner:ops` | |
| Envelope `id` | ULID | `01JC9Q3W8M2X7ZK4T5R6Y80001` |
| Record ids | `<prefix>_<[A-Za-z0-9_-]{1,64}>` — `prp evl dec apr act ver grt job corr obj`; ULID suffix recommended | `dec_01JC9Q4…` |
| Times | RFC 3339, UTC, `Z` suffix | `2026-11-06T18:20:11Z` |
| Hashes / keys / signatures | `sha256:<hex>` · `ed25519:<base64url, no padding>` | |

Manifests carry the bare `component_id`; messages may carry instance references. Signature verification looks a signer up by its exact reference first, then by its `component_id`.

### Signatures and hashes

All of these operate on the **RFC 8785 (JCS) canonical JSON** of the object — see `autonomy_conformance/jcs.py` and `vectors/jcs/`. Integers beyond ±(2⁵³−1) are not allowed in signed content (send them as strings).

| Object | Signature field | Signer field | Signed bytes |
|---|---|---|---|
| Envelope | `autonomysig` | `source` | `JCS(envelope without autonomysig)` — includes `data` |
| Grant | `signature` | `issued_by` | `JCS(object without signature)` |
| Decision | `signature` | `issuer` | same |
| Approval | `signature` | `approver`, or `attested_by` when the approval service (C05) signed after verifying the owner | same |
| Control order | `signature` | `issued_by`, or `attested_by` | same |
| ExecutionRecord | `signature` | `executor` | same |
| Verification | `signature` | `verifier` | same |
| Audit record | `signature` | `actor` | same (so it covers `hash`) |
| Registry `owners` | `signature` | `owners.root` | `JCS(owners without signature)` — only root changes the owners |
| Registry signature (detached, `<name>.sig.json`) | `signature` | `key` (role in `signer`) | `JCS(signature document without signature)`; it binds `registry_hash` = `sha256(JCS(registry))` and `serial` |

- **`params_hash`** = `sha256(JCS(proposal.action))`, i.e. over `{"type", "target", "params"}`. Decisions and approvals carry it; C06 recomputes it from the proposal in the `ActionRequest`.
- **Audit `hash`** = `sha256(UTF-8(prev_hash) ‖ JCS(record without hash and signature))`, where `prev_hash` is the full `sha256:<hex>` string. The first record of a cell's chain has `seq` 0 and `prev_hash` `sha256:` followed by 64 zeros. Each next record has `seq + 1` and the previous `hash`.

### Registry rules

A cell accepts a registry when, in this order: the document is consistent (one key per identity across owners, nodes and components; every component's `cells` are nodes; revoked ids and keys are absent; nodes on listed sites); `registry_hash` matches; `owners.root` equals the root key the cell pinned (the first accepted registry pins it); root's `owners.signature` verifies; the detached signature is by `owner:root` with `owners.root`, or by `owner:ops` with a listed ops key whose `expires` is after `signed_at`; and `serial` is above the one held (equal only for the same document). Otherwise the cell keeps the last valid registry. Reasons are stable phrases shared by the Python and Go libraries: `invalid registry`, `registry hash mismatch`, `untrusted root`, `bad owners signature`, `unknown signing key`, `ops key expired`, `bad signature`, `rollback`, `serial conflict`.

Cells are given the exact JSON that was signed (`autonomy registry sign` writes `<name>.json` next to `<name>.sig.json`), never a re-read YAML copy: YAML timestamps and booleans would change the hash.

### What the CLI checks beyond the schemas

Envelope: unique `(source, seq)` and `(source, id)` across samples; `expires` after `time`; `type` ↔ `dataschema` agreement; taint — a proposal with any `untrusted` input must travel in an envelope with `trust: untrusted`. Manifest: contract ranges admit this spec version; no self-dependency; no duplicate provided capability; `proposes_actions` requires `action-lifecycle`. Grant: window and `value_range` order; never granted to the owner. ActionRequest: decision is for this proposal and binds its `params_hash`; approval is for this decision with the same `params_hash`; one `idempotency_key`. Audit: `hash` correct; genesis; chain continuity. Decision/approval/grant/execution: time order. Signatures, nested ones included, when keys are given.

Registry: root signature over `owners`, unique keys, components on listed nodes, revoked identities absent, nodes on listed sites. Registry signature: verifies with its own `key` (pairing with a document and the serial rules are the core library's job).

Schemas themselves enforce, among others: R3 is never grantable; unknown grant constraints and unknown decision obligations fail; `require_approval` carries an approval obligation and needs an approval to execute; `deny` cannot be executed; an evaluation is `ok` only if every check passed (advisory LLM checks can only escalate or reject, ADR-008); failed health checks state their `impact`; `validation.*` and `policy.*` errors are not retryable; manifests may read only `events:` and `api:` sources.

### Closed and open schemas

Documents a component **acts on with authority** are closed (`additionalProperties: false`) so an unknown field fails instead of being silently ignored: `manifest`, `registry` (top level; node entries carry edge_server settings), `registry-signature`, `grant`, `action-proposal`, `decision-request`, `decision`, `approval`, `action-request`, `error`. Reports and events are open, and consumers ignore unknown fields: `envelope`, `health`, `ready`, `cell-status`, `log-record`, `evaluation`, `execution-record`, `verification`, `audit-record`, `job`.

For a closed schema, a field added in a MINOR release may only be sent once the receiving component's manifest declares that MINOR (the registry knows both ranges).

### Versioning

- One version for the whole spec, `MAJOR.MINOR.PATCH`, released as git tags `spec-vX.Y.Z` (ADR-014). The package version and `SPEC_VERSION` match the tag.
- **MAJOR 0 is a real major.** `/v0/` schemas keep the compatibility promise of design §14 from v0.1.0 on: `0.2` must accept everything `0.1` accepted. Ranges follow that: `^0.1` means `>=0.1.0 <1.0.0` (unlike npm's 0.x rule), `~0.1` means `0.1.x`.
- Allowed in a MINOR: add an optional property, a new schema, a new event type, or loosen a bound. Breaking (new MAJOR directory `v1/`, an ADR, and 6 months' support for both): remove or rename a property, change a type, `const`, `pattern` or `$ref`, add a required property or an enum value consumers must handle, tighten any bound or add a constraint. An enum marked `"x-autonomy-open-enum": true` may grow in a MINOR; v0.1 marks none.
- `autonomy-conformance compat` enforces this against the previous `spec-v0.*` tag in CI. It is conservative: a change it does not understand counts as breaking.

## Develop

```bash
cd spec
python -m pytest                       # unit tests (needs jsonschema, referencing, cryptography, pytest)
python -m autonomy_conformance selftest
python tools/gen_fixtures.py           # after changing schemas or fixtures; commit the output
python tools/gen_fixtures.py --check
```

### CI and releases

[`.github/workflows/spec.yml`](../.github/workflows/spec.yml) runs on every PR and push to `main` touching `spec/`: unit tests (incl. every schema valid against Draft 2020-12) on Python 3.10 and 3.13, `selftest`, `gen_fixtures.py --check`, both example components at `--strict`, `compat` against the previous `spec-v0.*` tag, a wheel built and run from a clean venv outside the source tree, `shellcheck` and `actionlint`.

To release: set the version in `pyproject.toml`, `autonomy_conformance/__init__.py` and `SPEC_VERSION` (`schemas.py`); change `## X.Y.Z — unreleased` in CHANGELOG.md to a date; merge; then `git tag -s spec-vX.Y.Z && git push origin spec-vX.Y.Z`. [`spec-release.yml`](../.github/workflows/spec-release.yml) reruns all checks, refuses mismatched versions or an unreleased changelog entry, and publishes a GitHub release with the wheel.

`autonomy_conformance/schemas` is a symlink to `../schemas` so the wheel carries the schemas; on systems without symlinks set `AUTONOMY_SPEC_SCHEMAS=spec/schemas/v0`.

## Decisions made in v0.1 where the design was open

| Topic | Design said | v0.1 |
|---|---|---|
| Instance ids | §1: `<component_id>#<cell>`; examples use `@` | `@<cell>` everywhere; manifests use the bare id |
| Range semantics for 0.x | `^0.1` in examples, rules in §14 | v0 is a real major; `^0.1` = `>=0.1.0 <1.0.0` |
| Signed bytes, `params_hash`, audit hash input | "canonical JSON", "binds the exact parameters" | Exact rules above, with vectors |
| `heartbeat` event type | listed under `health.*` | `health.heartbeat` (types have ≥ 2 segments) |
| `expires` "required for commands" | — | Optional in the schema (a command cannot be told from a fact by name); receivers of commands require it |
| Job `privacy` (J5) | `local_only` | `privacy` on jobs uses the routing vocabulary `local_only`/`cloud_ok`; envelopes keep the classification `public`/`site`/`private`/`secret-adjacent` |
| Grant `max_risk_class` | — | Added; R3 rejected by the schema |
| `trust_class` values | example `trusted-input` | `trusted-input`, `untrusted-input` |
| Health `status` values | `ok`, `degraded` in examples | `ok`, `degraded`, `down`; checks may set `safety: true` |
| Owner rejection of an approval | — | Not modelled in v0.1: no approval before `expires` means no execution |
