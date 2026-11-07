# ADR-014: Design package and contracts live in `realsystem/autonomy`

**Status:** Accepted · 2026-11-06 (supersedes the provisional version that kept the package in `farm_automation`)

## Context

The package was first written into `farm_automation`, at the owner's request. It covers the whole system, not only the farm, and readers of the farm repository could mistake it for farm scope. The owner created `realsystem/autonomy` and asked for the package to move there, with Autonomy work continuing in its own thread.

## Decision

- The design package lives in `realsystem/autonomy` under `design/`.
- C01 Contracts will live in the same repository under `spec/`, with its own version tags (`spec-vX.Y.Z`), so components can pin contract versions independently of design edits.
- `farm_automation` keeps the Field Station design and a pointer to this repository.

## Alternatives

| Alternative | Why not (now) |
|---|---|
| Keep the package in `farm_automation` | Mixes system scope with one deployment |
| Separate `autonomy-spec` repository for contracts | More repositories to keep in step; can be split later if release cadence demands it |

## Consequences

One place for the system design and its contracts. Farm work and Autonomy work are tracked separately.

## Evidence and references

Owner instruction, 2026-11-06.

## Revisit when

Contract releases need an independent repository (e.g. external contributors, or tooling that expects one package per repository).
