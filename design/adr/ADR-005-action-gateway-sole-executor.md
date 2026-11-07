# ADR-005: The Action Gateway is the sole executor of consequential actions

**Status:** Accepted · 2026-11-06

## Context

The brief requires separating proposing, evaluating, authorizing, executing and verifying. `edge_server`'s design places 'a policy check and executor between the agent and the host helpers' [V].

## Decision

Only C06 holds write credentials for adapters on its cell. It executes R1+ actions only with a valid signed Decision (and Approval where required), re-checks policy, enforces idempotency, rate limits, breakers and power gates, and emits ExecutionRecords. Verification is separate (C14).

## Alternatives

| Alternative | Why not (now) |
|---|---|
| Agents call adapters with scoped tokens | Proposer would hold execution authority |
| Job Service executes actions | Mixes compute and authority; Job Service is not on every cell |

## Consequences

One more hop per action; credential placement becomes the enforcement mechanism.

## Evidence and references

edge_server design; brief §2.5 [V]. See [research-and-references.md](../research-and-references.md).

## Revisit when

Never, for R1+; R0 reads stay direct.
