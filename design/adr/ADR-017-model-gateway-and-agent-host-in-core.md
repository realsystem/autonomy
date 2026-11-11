# ADR-017: Model Gateway and Agent Host built in `autonomy-core`; `jarvis` out of scope

**Status:** Proposed · 2026-11-11 · supersedes [ADR-012](ADR-012-model-gateway-library.md) when accepted

## Context

ADR-012 seeded C12 from the `LLMProvider` port in `jarvis`, and the roadmap planned to port C11 from the `jarvis` orchestrator and the `farm_agent` loop. The owner has taken `jarvis` out of Autonomy's scope (2026-11-11): it is neither a source of code nor a consumer, and it gets no milestone. Work happens in `autonomy` and `edge_server`. The other app repos stay with the owner.

What C12 and C11 must deliver does not change (component-catalog §C11–C12, IT-83, ST-03):
- Provider independence.
- Privacy routing, so that `local_only` never leaves the cell.
- Fallback to local models, then to the caller's own degradation.
- Budgets and usage accounting.
- Agents that act only through proposals.

## Decision

**C12 Model Gateway: a library in `autonomy-core` (`autonomy_core.models`), designed from the contracts and not ported.**
- **One port:** `complete(ModelRequest) → ModelResult | unavailable.model`. A request carries messages, `task_class`, `privacy`, optional tools, an optional JSON output schema, `max_tokens` and a deadline. A result carries the text or structured output, the model used, the route taken and usage. The request and the usage record become spec schemas (`model-request`, `model-usage`) in the next spec minor.
- **Two wire adapters, no vendor SDKs.** Both use only the standard library HTTP client, avoiding the SDK drift seen in `farm_agent`:
  - OpenAI-compatible Chat Completions, which covers Ollama, llama.cpp, vLLM, OpenRouter and most clouds;
  - native Anthropic Messages.
- **Endpoints are declared in config.** Each has a `locality` (`cell`, `site`, `cloud`), a host allowlist and a secret reference (`secret://`, ADR-013).
- **Routing table:** a versioned file mapping `task_class` to an ordered chain of endpoints. Privacy is enforced before any connection is opened:
  - `local_only` / `private` / `secret-adjacent` → `cell`/`site` endpoints only;
  - `cloud_ok` → any endpoint.
  - The HTTP layer also refuses any host that is not on the chosen endpoint's allowlist (defence in depth for IT-83).
- **Fallback:** the next endpoint on `unavailable.*` or a timeout, with a per-endpoint circuit breaker (as in C06). At the end of the chain the caller gets `unavailable.model` and degrades (ST-03). Safety, policy and execution never wait on a model.
- **Budgets:** tokens and cost per agent per day, plus a per-request deadline. Usage goes into the C17 telemetry and a usage log. Prompts are not stored by default, and logs pass through the redactor.
- **Structured output:** when a schema is requested, the reply is validated, with one retry on failure. If it still fails: `unavailable.model.invalid_output`.
- **Fake provider and recorded replies** for tests.
- **Optional hub service later:** a thin HTTP wrapper on the hub can expose the same port to applications (M22). LiteLLM stays the revisit option if many providers or central key management are needed.

**C11 Agent Host: a thin host in `autonomy-core` (`autonomy_core.agents`); no agent framework.**
- **Manifests:** an agent is a manifest (the existing `manifest` schema: identity, capabilities with `privacy`, read tools, the action types it may *propose*, budgets) plus a Python `run(context)` function.
- **Runs:** each run is triggered by a schedule, an event or an owner question.
  - The host binds only the read tools declared in the manifest. Tool output is wrapped as data, never as instructions.
  - Model calls go through C12 under the agent's privacy and budget.
  - Anything consequential becomes an `ActionProposal`, sent through C13 → C04 → C05 → C06 → C14 as built in M5–M8. Agents hold no adapter credentials.
- **Control:** quarantined agents get read tools only. A suspend or hold control order (M6) stops runs, abandons the in-flight model call and cancels the agent's pending proposals (C11 acceptance).
- **Memory:** a small per-agent store (SQLite, C15-lite) owned by the agent.

**C13 LLM review** (the M8 hook) becomes a `ModelReviewer` on C12. It uses the proposal's privacy, task class `review` and a JSON output schema `{verdict, note}`, and remains escalate-only (ADR-008).

**First consumer in scope: `edge_server`.**
- `/ask` answers questions about the node from read tools (status, audit, Frigate stats). It may *propose* `/restart` through the governed chain.
- The LLM reviewer is switched on for host actions.
- A01 (`farm_agent`) can adopt the same host later, on the owner's side.

## Alternatives

| Alternative | Why not (now) |
|---|---|
| Port the `jarvis` port and orchestrator (ADR-012) | Out of scope by owner decision |
| LiteLLM proxy as C12 | An extra service on small cells, a large dependency surface, and privacy enforced outside the cell's own process. Remains the revisit option |
| Provider SDKs (openai, anthropic) | Version drift already seen; heavier installs on the Pi and HA add-ons; two small wire adapters cover the need |
| An agent framework (LangGraph, PydanticAI, …) as the host | Couples contracts to a fast-moving framework. Agents may still use one internally if they emit Autonomy contracts |

## Consequences

- We maintain two HTTP adapters and a routing table.
- Privacy is enforced in-process, before connecting, and is testable with a socket guard (IT-83).
- The roadmap's M7 no longer depends on any external repository.
- M21 (voice via `jarvis`) is removed. A voice channel, if wanted later, is a new C21 client of the same APIs.

## Revisit when

- Many providers or centralised key management are needed → LiteLLM behind the C12 port.
- Several applications need models from outside the hub → the C12 hub service.
