# ADR-002: Contracts first: JSON Schema + CloudEvents envelope

**Status:** Accepted · 2026-11-06

## Context

No repository shares a contract today [V]. Components will be written in Python, Go, TypeScript and Rust over years.

## Decision

Create `autonomy/spec` with JSON Schema 2020-12 definitions and a conformance CLI. All cross-component messages use CloudEvents 1.0 structured JSON with Autonomy extension attributes and an Ed25519 signature.

## Alternatives

| Alternative | Why not (now) |
|---|---|
| Protobuf/gRPC | Better typing, but harder to read, sign canonically and debug by hand; adds codegen to every repo |
| AsyncAPI only | Describes channels, not a message envelope; can be added later as documentation |

## Consequences

Every repo gains a CI step; schemas must be versioned carefully (compat checker).

## Evidence and references

CloudEvents spec [R]; inventory finding 'no shared contracts' [V]. See [research-and-references.md](../research-and-references.md).

## Revisit when

Throughput or payload size makes JSON a measurable bottleneck.
