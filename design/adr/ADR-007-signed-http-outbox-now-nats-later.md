# ADR-007: Signed HTTP + SQLite outbox now; NATS JetStream later

**Status:** Provisional · 2026-11-06

## Context

`edge_server` already implements signed HTTP between nodes with an outbox and seen-store [V]. Cell count is small.

## Decision

C09 uses per-cell SQLite event logs, signed HTTP sync with per-producer sequence acks, and local pull subscriptions. No broker.

## Alternatives

| Alternative | Why not (now) |
|---|---|
| NATS JetStream with leaf nodes | Excellent edge store-and-forward, ~15 MB binary [R], but another service per cell |
| MQTT | Good for device telemetry; weak cross-site durable replay |
| Kafka/Redis Streams | Too heavy / not edge-friendly |

## Consequences

Fan-out and replay are implemented by us in a limited way.

## Evidence and references

edge_server controlplane.py [V]; Synadia docs [R]. See [research-and-references.md](../research-and-references.md).

## Revisit when

> 10 cells, > 5 consumers per topic, sustained > 50 msg/s, or a second developer team.
