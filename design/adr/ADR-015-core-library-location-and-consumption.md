# ADR-015: Core library (C02) lives in `realsystem/autonomy/core/`

**Status:** Proposed · 2026-11-08

## Context

M2 extracts the control-plane primitives of `edge_server` (`telegram-bot/controlplane.py`) into one library, in Python and Go (ADR-011), so every component signs and accepts envelopes identically. The library must track the contracts in `spec/` closely: its tests run against `spec/vectors/` and `spec/fixtures/`. Deployed nodes build their images on the device, and the `edge_server` bot loads its code from mounted files, not from a package index.

## Decision

- Python package `autonomy-core` (import `autonomy_core`) in `core/python/`; Go module `github.com/realsystem/autonomy/core/go` (package `autonomycore`) in `core/go/`.
- Releases are tags: `core-vX.Y.Z` for Python and `core/go/vX.Y.Z` for Go (the Go convention for a module in a subdirectory). Both carry the same version while they implement the same spec version.
- Consumers pin an exact commit or tag. Python consumers install from the GitHub archive URL (`autonomy-core @ https://github.com/realsystem/autonomy/archive/<ref>.tar.gz#subdirectory=core/python`); no package index is needed.
- The library depends only on `cryptography` (Python) and the standard library (Go). It does not depend on the conformance kit, and the kit does not depend on it; shared vectors keep them equal.

## Alternatives

| Alternative | Why not (now) |
|---|---|
| Separate `autonomy-core` repository | Vectors and fixtures would have to be copied or fetched; one more repository to keep in step. Can be split when release cadence demands it |
| Publish to PyPI | Private ecosystem, one owner; pinned archive URLs are enough and avoid a publishing credential |
| Vendor a copy into each consumer | Copies drift; consumers would need a sync check against upstream anyway |

## Consequences

Contract changes and their library changes land in one PR, tested together. Consumers need network access when building images (already true for `cryptography`).

## Evidence and references

M2 in [development-roadmap.md](../development-roadmap.md); C02 in [component-catalog.md](../component-catalog.md); [integration-strategy.md](../integration-strategy.md).

## Revisit when

A second team releases the library on its own cadence, or a consumer cannot reach GitHub when building.
