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
- **Three adapters, no vendor SDKs.** All use only the standard library HTTP client, avoiding the SDK drift seen in `farm_agent`:
  - **OpenAI-compatible Chat Completions** (synchronous): Ollama on the GPU PC over the LAN, plus llama.cpp, vLLM, OpenRouter and most clouds;
  - **Anthropic Messages** (synchronous);
  - **`processor` job** (asynchronous, see "Local inference through `processor`" below).
- **Endpoints are declared in config.** Each has a `locality` (`cell`, `site`, `owned`, `cloud`), a host allowlist and a secret reference (`secret://`, ADR-013).
- **Routing table:** a versioned file mapping `task_class` to an ordered chain of endpoints. Privacy is enforced before any connection is opened:
  - `local_only` / `private` / `secret-adjacent` → `cell`/`site` endpoints only;
  - `site` data → `cell`/`site`/`owned`;
  - `cloud_ok` → any endpoint.
  - The HTTP layer also refuses any host that is not on the chosen endpoint's allowlist (defence in depth for IT-83).
- **Fallback:** the next endpoint on `unavailable.*` or a timeout, with a per-endpoint circuit breaker (as in C06). At the end of the chain the caller gets `unavailable.model` and degrades (ST-03). Safety, policy and execution never wait on a model.
- **Budgets:** tokens and cost per agent per day, plus a per-request deadline. Usage goes into the C17 telemetry and a usage log. Prompts are not stored by default, and logs pass through the redactor.
- **Structured output:** when a schema is requested, the reply is validated, with one retry on failure. If it still fails: `unavailable.model.invalid_output`.
- **Fake provider and recorded replies** for tests.
- **Optional hub service later:** a thin HTTP wrapper on the hub can expose the same port to applications (M22). LiteLLM stays the revisit option if many providers or central key management are needed.

**Local inference through `processor` (C07).** The GPU PC already runs a `processor` worker with Ollama.
- What it gives: it polls its control plane outbound only, so nothing on the GPU PC is exposed. Work is durable: leases, retries, a dead-letter queue, and capability-based placement (`WORKER_CAPABILITIES`). Jobs simply wait while the PC is off or asleep.
- C12 uses it in two ways:
  - **Interactive tasks** (the M8 reviewer, `/ask`) call Ollama on the GPU PC **directly over the LAN** through the OpenAI-compatible adapter. Seconds matter, and the queue's 5 s polling plus its async-only design ("no synchronous inference endpoints") adds latency.
  - **Batch tasks** (the daily farm assessment, summaries, embeddings, longer reports) are submitted as `processor` jobs and collected when done, waiting at most the request's deadline. Through C07 they get durability and placement for free.
- **Locality follows the control plane.** Job inputs and results are stored in the control plane's SQLite.
  - With the control plane on the **VPS**, a prompt leaves the site, even though the VPS is the owner's. That endpoint is `locality: owned` (off-site, owner-controlled). It may serve `public`/`site` data and `cloud_ok` tasks, but **never** `private` or `local_only`.
  - Private batch work needs a control plane at home: a second `processor` control plane on the home server, which is what architecture §"Deployment units" already allows ("VPS or home hub"). The GPU PC worker polls it over the LAN, with `locality: site`.
  - The endpoint's locality is declared in the routing config next to the control plane URL.
- **What `processor` needs first.** All of this is C07 work (M4), in the owner's repository, done by or with the owner:
  1. **A typed `llm.chat@1` job type** whose input is the C12 `ModelRequest`, carrying messages, a system prompt, `format`/JSON schema and options, sent to Ollama `/api/chat`. The current `ollama_chat` executor sends one free-form `prompt` to `/api/generate`, defaults to `llama3.2`, and drops token usage.
  2. **No prompt logging.** The executor currently logs the full prompt. **Retention:** LLM job inputs and results are purged after a TTL (default 24 h) and kept out of webhooks and e-mail.
  3. **The M4 items:** idempotency key, `traceparent`, and per-worker Ed25519 identity instead of the shared worker token.

  Until these exist, C12 uses the direct LAN path only, and batch tasks run through it with a longer deadline.
- **Locality values:** `cell` (this machine), `site` (this LAN), `owned` (off-site, owner-controlled: the VPS), `cloud` (third-party).

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
| `review` (M8 reviewer) | local only | GPU PC Ollama over LAN → none (review `skipped`, recorded) |
| `ask.node` (`/ask` about the node) | cloud OK | GPU PC Ollama over LAN → cloud |
| `assess.farm` (daily, batch) | private | home `processor` → `llm.chat@1` on GPU PC → none (assessment skipped, owner told) |
| `reason.hard` (owner Q&A) | cloud OK | cloud → other cloud → GPU PC Ollama |

The Frigate server runs no model: inference stays off the machine that does camera detection.

## Alternatives

| Alternative | Why not (now) |
|---|---|
| Port the `jarvis` port and orchestrator (ADR-012) | Out of scope by owner decision |
| Expose Ollama on the GPU PC to the VPS / cloud | Opens an inbound path to the GPU PC; `processor` polling already solves reachability |
| `processor` jobs for every model call | Polling and the async-only design add seconds to interactive calls in the decision path; the direct LAN path is used for those |
| LiteLLM proxy as C12 | An extra service on small cells, a large dependency surface, and privacy enforced outside the cell's own process. Remains the revisit option |
| Provider SDKs (openai, anthropic) | Version drift already seen; heavier installs on the Pi and HA add-ons; two small wire adapters cover the need |
| An agent framework (LangGraph, PydanticAI, …) as the host | Couples contracts to a fast-moving framework. Agents may still use one internally if they emit Autonomy contracts |

## Consequences

- We maintain three small adapters and a routing table.
- M7's batch path depends on `processor` changes (M4 items plus `llm.chat@1`) in the owner's repository; the interactive path does not.
- Privacy is enforced in-process, before connecting, and is testable with a socket guard (IT-83).
- The roadmap's M7 no longer depends on any external repository.
- M21 (voice via `jarvis`) is removed. A voice channel, if wanted later, is a new C21 client of the same APIs.

## Revisit when

- Many providers or centralised key management are needed → LiteLLM behind the C12 port.
- Several applications need models from outside the hub → the C12 hub service.
