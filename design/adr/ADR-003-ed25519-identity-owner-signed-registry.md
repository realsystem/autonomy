# ADR-003: Ed25519 identities and an owner-signed registry

**Status:** Accepted · 2026-11-06

## Context

`edge_server` already generates per-node Ed25519 keys, signs canonical JSON and checks freshness/replay [V]. Autonomy needs identity for components and agents too.

## Decision

Generalise `fleet.yaml` into an owner-signed registry listing cells, components and agents with public keys, manifest hashes and capabilities. Owner root key (offline) signs ops-key delegations; ops key signs registry updates.

## Alternatives

| Alternative | Why not (now) |
|---|---|
| SPIFFE/SPIRE | Datacenter-scale; extra server and agents |
| OAuth/OIDC identity provider | Needs an always-on IdP; bearer tokens |
| mTLS PKI | Certificate lifecycle overhead for a single owner |

## Consequences

Simple and offline-capable; key rotation and revocation are file updates; enrollment requires an out-of-band fingerprint check.

## Evidence and references

edge_server controlplane.py, fleet.schema.json [V]. See [research-and-references.md](../research-and-references.md).

## Revisit when

Multiple owners/organisations, or Kubernetes deployment.
