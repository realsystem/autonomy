# ADR-012: Model Gateway as a library from `jarvis`; no agent framework dependency

**Status:** Accepted · 2026-11-06 · *to be superseded by [ADR-017](ADR-017-model-gateway-and-agent-host-in-core.md) (Proposed): `jarvis` is out of scope since 2026-11-11, so C12 is no longer seeded from it*

## Context

`jarvis` defines an `LLMProvider` port with two adapters [V]; four repos call Ollama directly [V]; `farm_agent` shows SDK drift [V].

## Decision

C12 is a Python library implementing the `jarvis` port with routing by privacy/task/cost, fallbacks and budgets; Ollama for local models. Agents may use any framework internally but must emit Autonomy contracts.

## Alternatives

| Alternative | Why not (now) |
|---|---|
| LiteLLM proxy | Many providers; extra service and large dependency surface |
| Adopt an agent framework platform-wide | Couples contracts to a fast-changing framework |

## Consequences

We maintain a few adapters; privacy routing is enforced in-process.

## Evidence and references

jarvis architecture [V]; LiteLLM/Ollama docs [R]. See [research-and-references.md](../research-and-references.md).

## Revisit when

Need for many providers or centralised key management → LiteLLM behind C12.
