# ADR-004: Cedar as policy language with an embedded PDP

**Status:** Provisional · 2026-11-06

## Context

Authorization must be evaluated locally on every cell, be auditable and be readable by one owner. No policy engine exists in the repos [V].

## Decision

Write policies in Cedar, validate them in CI against a schema, sign bundles, and evaluate them in-process (`cedar-go` in Go, `cedarpy` in Python) behind the C02 `Pdp` interface.

## Alternatives

| Alternative | Why not (now) |
|---|---|
| OPA/Rego | General-purpose, CNCF, large ecosystem; harder to read and analyse for app-level authorization [R] |
| OpenFGA | Relationship graphs not needed |
| Hand-written code | Not analysable; policy changes need releases |

## Consequences

Small runtime; deny by default; Python binding maturity is a risk (R-04).

## Evidence and references

Permit.io comparisons and Cedar paper [R]. See [research-and-references.md](../research-and-references.md).

## Revisit when

Python binding unreliable, or policies need data lookups Cedar cannot express → OPA.
