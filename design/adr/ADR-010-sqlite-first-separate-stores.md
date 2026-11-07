# ADR-010: SQLite first, separate stores by data kind

**Status:** Accepted · 2026-11-06

## Context

Brief requires distinguishing operational state, memory, documents, knowledge and events. Existing repos use SQLite (`processor`, `yt_sync`) and Postgres (`gardening-service`, `wildwatch`, planned in `jarvis`) [V].

## Decision

Each component owns its SQLite database (WAL). C15 uses one SQLite file per space with FTS5 and a vector extension. Postgres is used where an application already uses it, and for C15 when concurrency requires it. No separate vector database.

## Alternatives

| Alternative | Why not (now) |
|---|---|
| Postgres everywhere | Extra service on the Pi and VPS |
| Dedicated vector DB | Another service before any measured need |

## Consequences

Backups via Litestream/snapshots; deletion by file.

## Evidence and references

Repos [V]; Litestream docs [R]. See [research-and-references.md](../research-and-references.md).

## Revisit when

Concurrent writers or data volume exceed SQLite comfort → Postgres + pgvector.
