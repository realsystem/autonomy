# ADR-013: Secrets: per-cell stores + SOPS/age; no Vault now

**Status:** Accepted · 2026-11-06

## Context

`edge_server` has a secret store and keeps secrets out of `fleet.yaml` [V]; other repos use `.env`.

## Decision

Secrets referenced as `secret://<cell>/<name>`; stored per cell (root-only files or HA add-on password options); configuration in git encrypted with SOPS + age; CI secret scanning.

## Alternatives

| Alternative | Why not (now) |
|---|---|
| Vault/OpenBao | Another always-on stateful service to secure and back up |
| Cloud secret managers | Cloud dependency for cells |

## Consequences

Rotation is manual but rare; owner holds age keys.

## Evidence and references

edge_server secret_store.py [V]; Vault vs SOPS comparisons [R]. See [research-and-references.md](../research-and-references.md).

## Revisit when

> 10 cells, dynamic credentials, or multiple operators.
