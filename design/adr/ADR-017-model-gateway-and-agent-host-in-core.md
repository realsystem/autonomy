# ADR-017: Model Gateway and Agent Host built in `autonomy-core`; `jarvis` out of scope

**Status:** Proposed · 2026-11-11 · supersedes [ADR-012](ADR-012-model-gateway-library.md) when accepted

## Context

ADR-012 seeded C12 from the `LLMProvider` port in `jarvis`, and the roadmap planned to port C11 from the `jarvis` orchestrator and the `farm_agent` loop. The owner has taken `jarvis` out of Autonomy's scope (2026-11-11): it is neither a source of code nor a consumer, and it gets no milestone. Work happens in `autonomy`, `edge_server` and, since 2026-11-11, `processor` (the owner handed it over to adapt for Autonomy, provided it stays general-purpose). The other app repos stay with the owner.

What C12 and C11 must deliver does not change (component-catalog §C11–C12, IT-83, ST-03):
- Provider independence.
- Privacy routing, so that `local_only` never leaves the cell.
- Fallback to local models, then to the caller's own degradation.
- Budgets and usage accounting.
- Agents that act only through proposals.

## Decision

**C12 Model Gateway: a library in `autonomy-core` (`autonomy_core.models`), designed from the contracts and not ported.**
- **One port:** `complete(ModelRequest) → ModelResult | unavailable.model`. A request carries messages, `task_class`, `privacy`, optional tools, an optional JSON output schema, `max_tokens` and a deadline. A result carries the text or structured output, the model used, the route taken and usage. The request and the usage record become spec schemas (`model-request`, `model-usage`) in the next spec minor.
- **Three adapters, no vendor SDKs.** All use only the standard library HTTP client, avoiding the SDK drift seen in `farm_agent`:
  - **OpenAI-compatible Chat Completions** (synchronous): llama.cpp, vLLM, OpenRouter and most clouds, and Ollama if it is ever exposed directly;
  - **Anthropic Messages** (synchronous);
  - **`processor` job** (asynchronous, see "Local inference through `processor`" below).
- **Endpoints are declared in config.** Each has a `locality` (`cell`, `site`, `owned`, `cloud`), a host allowlist and a secret reference (`secret://`, ADR-013).
- **Routing table:** a versioned file mapping `task_class` to an ordered chain of endpoints. Privacy is enforced before any connection is opened:
  - `local_only` / `secret-adjacent` → `cell`/`site` endpoints only;
  - `private` / `site` data → `cell`/`site`/`owned`, which keeps it on the owner's machines and tailnet;
  - `cloud_ok` → any endpoint.
  - The HTTP layer also refuses any host that is not on the chosen endpoint's allowlist (defence in depth for IT-83).
- **Fallback:** the next endpoint on `unavailable.*` or a timeout, with a per-endpoint circuit breaker (as in C06). At the end of the chain the caller gets `unavailable.model` and degrades (ST-03). Safety, policy and execution never wait on a model.
- **Budgets:** tokens and cost per agent per day, plus a per-request deadline. Usage goes into the C17 telemetry and a usage log. Prompts are not stored by default, and logs pass through the redactor.
- **Structured output:** when a schema is requested, the reply is validated, with one retry on failure. If it still fails: `unavailable.model.invalid_output`.
- **Fake provider and recorded replies** for tests.
- **Optional hub service later:** a thin HTTP wrapper on the hub can expose the same port to applications (M22). LiteLLM stays the revisit option if many providers or central key management are needed.

**Local inference through `processor` (C07): owner topology, 2026-11-11.**

| Machine | Runs | Reached |
|---|---|---|
| Home server with GPU | Ollama (localhost only) + `processor` worker (`llm.chat`, `ollama_chat`) | Not reachable from outside; it polls outbound |
| Remote server running `edge_server` | `processor` control plane (`INSTALL_PROCESSOR=true`, edge_server #37); the bot; later the C12 client | Its port is published on its Tailscale address only |

There is no VPS in this path. The GPU worker polls the edge server's control plane over the owner's tailnet, with a per-worker Ed25519 key (`WORKER_AUTH=signature`).

- **Every model call goes through the queue,** including interactive ones:
  - Callers submit `llm.chat` jobs and long-poll the result (`GET /jobs/{id}?wait=10`).
  - Interactive task classes (the reviewer, `/ask`) use a high priority, and the worker polls every 1–2 s.
  - Ollama itself is never exposed. A direct OpenAI-compatible endpoint remains possible later if latency demands it.
- **What `processor` provides since #1–#2 (2026-11-11),** all opt-in so processor stays general-purpose:
  - idempotency keys and W3C `traceparent` / correlation id handed to the worker (IT-14, IT-20);
  - per-worker Ed25519 identities (IT-21);
  - `sensitive` jobs: no data in webhooks or e-mail, the worker logs ids only, purged after `retention_seconds` (default 24 h);
  - cancel, long-poll, queue stats and job types;
  - stuck-job recovery;
  - the `llm.chat` executor: Ollama `/api/chat` or any OpenAI-compatible server, messages, JSON schema output, token usage, no prompt logging;
  - images on GHCR;
  - CI.
- **Locality values:**
  - `cell`: this machine;
  - `site`: this LAN;
  - `owned`: the owner's machines at other sites, reached only over the owner's tailnet (the edge server, the home GPU server);
  - `cloud`: third-party.
- **Data at rest:** job inputs and results are stored on the control plane (the edge server) until they are purged. The C12 client therefore submits `private` work as `sensitive` with a short retention.

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

**Example routing table** (the first M7 configuration):

| Task class | Privacy | Chain |
|---|---|---|
| `review` (M8 reviewer) | private | `processor` `llm.chat`, high priority, on the home GPU → none (review `skipped`, recorded) |
| `ask.node` (`/ask` about the node) | cloud OK | `processor` `llm.chat` on the home GPU → cloud |
| `assess.farm` (daily, batch) | private | `processor` `llm.chat`, `sensitive`, on the home GPU → none (assessment skipped, owner told) |
| `reason.hard` (owner Q&A) | cloud OK | cloud → other cloud → `processor` `llm.chat` |

The edge server runs no model: inference stays off the machine that does camera detection. It only hosts the queue.

## Alternatives

| Alternative | Why not (now) |
|---|---|
| Port the `jarvis` port and orchestrator (ADR-012) | Out of scope by owner decision |
| Expose Ollama on the GPU server to the tailnet | An inbound path to the GPU server; `processor` polling already solves reachability. Kept as a fallback if queue latency is too high |
| A public VPS for the control plane | Prompts would be stored off the owner's machines; the edge server already runs 24/7 on the tailnet |
| LiteLLM proxy as C12 | An extra service on small cells, a large dependency surface, and privacy enforced outside the cell's own process. Remains the revisit option |
| Provider SDKs (openai, anthropic) | Version drift already seen; heavier installs on the Pi and HA add-ons; two small wire adapters cover the need |
| An agent framework (LangGraph, PydanticAI, …) as the host | Couples contracts to a fast-moving framework. Agents may still use one internally if they emit Autonomy contracts |

## Consequences

- We maintain three small adapters and a routing table.
- M7 depends on `processor` (C07). The owner handed it over on 2026-11-11, and the changes it needed are merged (processor #1, #2), so M4's identity, idempotency and trace items are covered.
- Privacy is enforced in-process, before connecting, and is testable with a socket guard (IT-83).
- The roadmap's M7 no longer depends on any external repository.
- M21 (voice via `jarvis`) is removed. A voice channel, if wanted later, is a new C21 client of the same APIs.

## Revisit when

- Many providers or centralised key management are needed → LiteLLM behind the C12 port.
- Several applications need models from outside the hub → the C12 hub service.
