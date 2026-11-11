# Changelog — Autonomy contracts (`spec`)

Releases are tagged `spec-vX.Y.Z`. Rules: [README.md#versioning](README.md#versioning).

## 0.5.0 — unreleased (M9)

- `audit-record.trace_id` (optional): the W3C trace id of the action story (IT-71).

## 0.4.0 — unreleased (M6)

Backward compatible with 0.3.

- `approval.attested_by` (optional): the approval service that verified the owner and signed.
- New schema `control-order` (`suspend`, `hold`, `resume`; scope `all`, a cell or a component); event types `system.suspended` / `system.resumed` carry it.

## 0.3.0 — unreleased (M5)

Backward compatible with 0.2.

- New schema `policy-bundle` (contract `action-lifecycle`): owner-signed grants, Cedar policies (one statement each, optionally `approvable`) and resource groups, versioned `YYYY.MM.DD-n`. Conformance rules and fixtures.

## 0.2.0 — unreleased (M3)

Backward compatible with 0.1 (`compat` reports only additions).

- New schemas `registry` and `registry-signature` (contract name `registry`): owner keys with root-signed delegation of ops keys, cells (edge_server fleet nodes), components with key, version, manifest hash and cells, revocations; detached signature with `serial` for rollback protection.
- Conformance rules for both; fixtures (valid two-site registry, 11 invalid cases).
- Test key `cell:home-hub` added.

## 0.1.0 — 2026-11-08 (M1)

First frozen contracts.

- Schemas (`schemas/v0/`): common, manifest, envelope, health, ready, cell-status, error, log-record, grant, action-proposal, evaluation, decision-request, decision, approval, action-request, execution-record, verification, audit-record, job.
- `autonomy-conformance` CLI: `check`, `validate`, `selftest`, `compat`, `canonicalize`, `params-hash`, `schemas`.
- Exact rules for RFC 8785 canonicalization, signatures, `params_hash` and the audit hash chain, with vectors.
- Valid and invalid fixtures for every schema; draft example manifests for C07 Job Service and C10 Cell Runtime.
- Resolved design ambiguities: see [README.md#decisions-made-in-v01-where-the-design-was-open](README.md#decisions-made-in-v01-where-the-design-was-open).
