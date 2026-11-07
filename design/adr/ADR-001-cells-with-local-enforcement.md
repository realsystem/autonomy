# ADR-001: Federation of cells with local enforcement

**Status:** Accepted · 2026-11-06

## Context

The Field Station runs on Starlink with limited physical access; the brief forbids a single point of failure and requires essential operation without internet. `edge_server` already runs identical signed configuration on every node and judges health locally [V].

## Decision

Each machine or site is a cell running C10 with an embedded PDP (C04), Action Gateway (C06), event log (C09) and audit chain (C16). Hub services (Job Service, console, knowledge) are optional for local operation.

## Alternatives

| Alternative | Why not (now) |
|---|---|
| Central orchestrator | Hub/link failure stops everything; violates local-first |
| Central policy service queried live | Every action needs connectivity |

## Consequences

Authority is distributed as owner-signed documents; revocation latency is bounded by grant expiry; every cell needs the same small runtime.

## Evidence and references

edge_server fleet design and control plane [V]; brief §2.4, §2.6. See [research-and-references.md](../research-and-references.md).

## Revisit when

More than ~20 cells, or cells that cannot hold a full policy bundle.
