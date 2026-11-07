# ADR-006: Extend `processor` for jobs; defer a workflow engine

**Status:** Provisional · 2026-11-06

## Context

`processor` already provides a durable SQLite queue with leases, retries, DLQ and NAT-friendly workers [V]. Expected scale is tens of workflows per day.

## Decision

Refactor `processor` into C07 (idempotency, tracing, typed job types, signed workers). Implement workflows as two tables plus a coordinator executor. Do not deploy Temporal/DBOS/Restate now.

## Alternatives

| Alternative | Why not (now) |
|---|---|
| Temporal | Mature but needs a server cluster and database; too heavy for a 1 GB VPS |
| DBOS | Library, MIT, Postgres-backed [R]; strong candidate later |
| Restate | Single server; watch |

## Consequences

No new infrastructure; workflow features stay minimal.

## Evidence and references

processor code [V]; DBOS vs Temporal comparison [R]. See [research-and-references.md](../research-and-references.md).

## Revisit when

Workflows > 10 steps or with loops become common; > 1,000 executions/day; in-flight versioning pain; long timers at volume → adopt DBOS first.
