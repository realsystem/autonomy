# ADR-011: Python and Go; containers and systemd; no Kubernetes

**Status:** Accepted · 2026-11-06

## Context

Platform candidates are Python (`edge_server`, `jarvis`, `farm_agent`) and Go (`processor`) [V]. Targets include a Pi on HA OS and a 1 GB VPS.

## Decision

Keep Python for cells, agents, adapters and the gateway; Go for the Job Service and workers. Package as Docker/compose, systemd units and HA add-ons. Releases from CI-built signed tags.

## Alternatives

| Alternative | Why not (now) |
|---|---|
| Single language rewrite | Rewrites working software without need |
| Kubernetes/k3s/Nomad | Operational overhead without benefit at this scale |

## Consequences

Two languages need C02 in both; cross-language test vectors required.

## Evidence and references

Inventory [V]. See [research-and-references.md](../research-and-references.md).

## Revisit when

> 20 hosts or a need for bin-packing many services.
