# ADR-008: LLM review can only escalate; no LLM-vote authorization

**Status:** Accepted · 2026-11-06

## Context

The brief warns that several LLMs voting is not independent governance. Research shows LLM judge panels have strongly correlated errors [R].

## Decision

C13 runs deterministic checks first. An optional LLM critique (different model family where possible, structured inputs only) can turn `ok` into `escalate` or `reject`, never the reverse. Actions from untrusted inputs at R1+ require approval (GOV-007).

## Alternatives

| Alternative | Why not (now) |
|---|---|
| LLM panel vote | Correlated errors; false sense of independence |
| No LLM review | Loses a cheap tripwire |

## Consequences

Some useful automation waits for the owner; LLM failures never permit harm.

## Evidence and references

Apple ML Research; Beurer-Kellner et al.; CaMeL [R]. See [research-and-references.md](../research-and-references.md).

## Revisit when

Evidence of genuinely independent automated verifiers for a domain.
