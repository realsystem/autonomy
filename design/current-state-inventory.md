# Current-State Inventory

**Status:** Draft 0.1 · 2026-11-06 · Evidence tags: see [vision-and-principles.md](vision-and-principles.md#evidence-tags-used-throughout-this-package)

All 26 repositories below were cloned at their default branch HEAD on 2026-11-06 and inspected: README and design docs, dependency files, layout, code size, tests, and key source files for the platform candidates. Statements are **[V]** unless tagged otherwise. "LOC" counts `.py .go .ts .tsx .js .rs .kt .sh` files tracked in git.

Classification and target roles are summarized in [repository-mapping.md](repository-mapping.md).

## 1. Summary table

| Repository | Actual purpose (verified) | Stack | LOC | Tests | Last commit | Maturity |
|---|---|---|---:|---|---|---|
| `processor` | Split-system batch job platform: Go control plane (VPS, SQLite) + polling worker (GPU PC, Ollama) | Go, SQLite, HTML templates | 9.3k | 3 Go test files + 2 shell integration scripts; no CI | 2026-04-16 | Working MVP; strongest execution asset |
| `edge_server` | Ubuntu edge node deployer (Frigate, Tailscale, optional HA/MQTT), power plugins, Telegram bot, **fleet control plane (Ed25519 node keys, signed heartbeats)**, fleet design | Python, Bash, YAML schema | 27.6k | ~800 test functions, CI | 2026-11-03 | Most mature; actively developed |
| `jarvis` | Voice-first personal assistant: WebSocket voice terminal → Pipecat STT/LLM/TTS → core orchestrator; GCP deploy | Python (uv), Pipecat, Terraform | 4.4k | 35 tests, CI + deploy | 2026-10-30 | Local POC + MVP architecture |
| `farm_agent` | Home Assistant add-on: LLM answers questions about farm sensors via HA REST; custom conversation integration | Python, OpenAI SDK, HA add-on | 2.0k | Several test files, CI | 2026-09-26 | Prototype with known defects (own audit says "do not deploy") |
| `farm_automation` | Field Station monitoring/irrigation architecture (v1.2), Farm Core mapping file | Markdown, YAML | 0 | — | 2026-11-04 | Design baseline, no implementation |
| `knowledge` | **"YouTube Solution Bot"** — Telegram bot: YouTube search → captions/Whisper → LLM → structured answers. *Not a knowledge store* | Python, aiogram, faster-whisper, Ollama/OpenAI | 5.3k | 11 test files | 2026-04-07 | Working app |
| `talk` | **TQVR** — P2P messaging platform: Ed25519, QUIC, Kademlia DHT, trust engine, relay, Android client, botnet test harness | Rust (19 crates), Kotlin | 60.4k | 25 test files, CI | 2026-11-06 | Large, active, independent |
| `tts` | Offline TTS CLI using Coqui XTTS-v2 voice cloning | Python, PyTorch, Coqui TTS 0.22 | 0.9k | 2 | 2026-04-22 | Working tool |
| `youtube-processor` | Video → frames → local VLM (Qwen2.5-VL via Ollama) or YOLO+OpenCV → timeline and voiceover scripts; structured JSON mode | Python, OpenCV, yt-dlp, Ollama | 11.0k | 9 | 2026-05-03 | Working pipeline |
| `common_reflection` | Media production: episode texts, MP3/SRT assets, "unQuell" generative network animation generator | Python, assets (818 MB) | 17.0k | 8 | 2026-05-07 | Content repo + generator |
| `leadgen` | Multi-source lead monitoring and delivery service with Stripe plans | Python, FastAPI, SQLAlchemy, APScheduler, Telegram | 19.5k | 23 | 2026-11-06 | Active product |
| `leadgen_front` | Static portal (pricing, dashboard, leads browser) for `leadgen` | HTML/JS | 0.7k | 0 | 2026-04-04 | Companion front end |
| `wildwatch` | Route hazard monitoring (NWS, USGS, Cal511, Caltrans) with risk score, Stripe plans | Next.js, Prisma, PostGIS, Supabase | 17.7k | 19 + integration tests | 2026-10-29 | Active product; honest STATUS.md |
| `my_fin_structure` | Bank statement PDF parser → Google Sheets; LLM-generated bank providers via local Ollama; PDF redaction | Python, pdfplumber, gspread | 5.9k | 3 | 2026-09-22 | Working personal tool |
| `fuel_economy` | Two OCR scripts for fuel pump displays + sample images; no README | Python, OpenCV, Tesseract | 0.5k | 0 | 2026-10-29 | Experiment |
| `tires` | Off-road tire and gear ratio calculator (live at overlandn.com) | React, Vite | 10.9k | 12 | 2026-03-17 | Deployed utility |
| `wires` | 12/24 V wire gauge calculator | React, Vite | 0.8k | 1 | 2026-03-17 | Deployed utility |
| `thequeue` | Telegram queue-style communication bot | Python | 0.7k | 1 | 2025-06-13 | Dormant |
| `video_reprocessor` | Download, cut and H.265-compress YouTube videos | Python, yt-dlp, ffmpeg | 0.8k | 0 | 2026-09-14 | Small tool |
| `yt_sync` | "Autonomous agent" archiving a YouTube watchlist to Google Drive (observe → compare → plan → act loop), Android sync app | Python, SQLite, rclone, Kotlin | 2.8k | 1 | 2026-03-10 | Working tool |
| `gardening-service` | Gardening lifecycle SaaS MVP (seed → harvest), auth, care tasks | FastAPI, Postgres, React, Prometheus/Grafana | 57.9k | 80 | 2026-02-03 | Large MVP, dormant since Feb |
| `my_vps` | WireGuard VPN setup and a "VPN platform" for Ubuntu VPS | Bash, Python | 5.1k | 0 | 2026-03-10 | Ops scripts |
| `hw` | Bare-metal provisioning PoC (Terraform + Foreman + Ansible for Ceph) | Terraform, Ansible | 1.2k | 8 | 2026-03-19 | Unrelated PoC |
| `basic-complexity` | Empty README | — | 0 | — | 2026-10-19 | Placeholder |
| `book` | Three Markdown chapters | Markdown | 0 | — | 2026-05-26 | Writing |
| `business_ideas` | Market-check notes and startup playbook | Markdown | 0 | — | 2026-04-16 | Notes |

Not inspected in depth: older realsystem repositories (2015–2020, e.g. `homeassistant`, `arduino`, `fridge_control`, `terraform`, forks) and the `airshipit` organisation. They are listed as archive candidates in [repository-mapping.md](repository-mapping.md#4-not-analysed-in-depth).

---

## 2. Platform candidates — detailed analysis

### 2.1 `processor`

| Question | Finding |
|---|---|
| Problem | Run batch jobs on a private GPU machine while exposing a public API from a 1 GB VPS, with no inbound ports at home |
| Implements | Control plane (Go): users, sessions, API keys, jobs, job_attempts, webhooks (HMAC-signed, retried), email notifications, workers, `audit_log`, admin UI. Data plane (Go): polling worker with `Executor` registry and executors `noop`, `sleep`, `echo_metadata`, `ollama_chat` |
| Interfaces | REST `/api/v1/jobs`, `/api-keys`, `/webhooks`; worker API `/worker/poll`, `/heartbeat`, `/jobs/:id/start|complete|fail`. Worker sends `capabilities[]` on poll |
| Data owned | SQLite (WAL): users, jobs (status, priority, lease, attempts, max_attempts), attempts, webhooks, deliveries, emails, workers, audit_log |
| Depends on | Nothing else in the inventory |
| Duplication | Auth/users duplicate `leadgen`, `gardening-service`, `wildwatch`. Ollama client duplicates `knowledge`, `youtube-processor`, `my_fin_structure` |
| Reusable | Lease-based queue, retry/backoff, DLQ, worker poll model (works through NAT), webhook signing, audit table |
| Needs refactoring | No idempotency key on job creation; no trace/correlation id; `Executor.Execute(ctx, jobType, map)` has no schema or version per job type; jobs are owned by *users*, not by components/agents; worker auth is a shared token, not a per-worker key; no CI |
| Keep independent | The control-plane/data-plane split |
| Target role | **C07 Job Service** — refactor (see [execution-and-workflows.md](execution-and-workflows.md)) |

### 2.2 `edge_server`

| Question | Finding |
|---|---|
| Problem | Deploy and keep alive headless edge nodes (NVR, power monitoring, HA) at remote sites, reporting to operators via Telegram |
| Implements | Deploy scripts and bootstrap over SSH; secret store; Frigate + camera discovery; Victron SmartShunt, Renogy Rover and NUT plugins; 1,966-line Telegram bot; `controlplane.py` (660 lines): Ed25519 key handling, canonical JSON signing, freshness and replay checks (`SeenStore`), persistent per-sender `Counter`, `Outbox`, `Peer`, signed HTTP; `tools/fleet.py` (993 lines) with `fleet.yaml` JSON Schema validated in CI |
| Designed, not yet built | Roles; incidents in SQLite; four-layer auto-repair (L0 hardware → L1 fixed actions → L2 agent → L3 operator); action tiers T0–T3; TOTP-gated approvals with request hash and expiry; incident agent restricted to an allowlist and run limits; power stages; archive role |
| Interfaces | Telegram commands; signed HTTP control port bound to Tailscale/LAN; `fleet.yaml` |
| Data owned | Node secrets, state file, outbox/seen stores, (planned) incident SQLite |
| Reusable | **Node identity and signed message contract, outbox, freshness checks, approval flow, action tiers, power-aware gating, "never cut your own access", rate limits and circuit breakers.** These are core Autonomy governance and runtime primitives already designed and partly implemented |
| Needs refactoring | Bot is one large file (design already plans a package split); the message contract is specific to fleet messages and should be generalised to the Autonomy envelope; approvals are tied to Telegram TOTP only |
| Target role | **C10 Cell Runtime** and seed code for **C02 Core Library**, **C05 Approval**, **C06 Action Gateway** (refactor + extract) |

### 2.3 `jarvis`

| Question | Finding |
|---|---|
| Problem | Persistent, provider-independent voice assistant across locations |
| Implements | Gateway (WebSocket), voice session (Pipecat-confined), VAD, STT/TTS factories, `LLMProvider` port with adapters, rule-based LLM fallback, `Orchestrator`, `ConversationStore` (142 lines), observability stub; protocol v1 doc; 3 ADRs (Proposed) |
| Planned | Postgres + pgvector memory; `tools/` with `Tool.permission ∈ {read, write, sensitive}`; tasks worker; Secret Manager |
| Data owned | Conversation turns, devices, sessions (planned Postgres) |
| Reusable | `LLMProvider` port shape (streaming events `TextDelta | ToolCall | Done | Error`), two-adapter rule, turn latency metrics, tool permission levels |
| Concern | Central GCP backend is the only brain; tool phase would make it a second, competing control plane if it calls Home Assistant directly |
| Target role | **C21 Human Interfaces** (voice channel) as an independent application; its `LLMProvider` port seeds **C12 Model Gateway**; its tools must go through **C06** |

### 2.4 `farm_agent`

| Question | Finding |
|---|---|
| Problem | Ask natural-language questions about farm sensors through HA Assist |
| Implements | `agent.py` (352 lines): OpenAI Chat Completions loop (model `gpt-4o-mini`), tools `discover_entities` and `get_entity_state`, hard-coded allowlist `sensor.eco_worthy_*` / `sensor.smartshunt_*`; HTTP server on 8080; HA custom conversation integration |
| Defects found | README says "OpenAI Agents SDK"; code uses plain Chat Completions. `system_prompt` is defined but never sent. Tool results are appended in Anthropic format (`type: tool_result`, `tool_use_id`) instead of OpenAI `role: tool` + `tool_call_id`, so multi-step tool use fails. Repository's own `AUDIT_SUMMARY.md` marks it "do not deploy" (localhost between add-on containers; custom integration provisioning) |
| Data owned | None |
| Target role | Becomes **A01 Field Station Agent** after refactor: read-only first, tools via C20/C06, model via C12 |

### 2.5 `knowledge`

Misleading name: it is a Telegram question-answering bot over YouTube videos. Its useful parts (search, caption extraction, Whisper fallback, LLM extraction with confidence, multi-video merge, cache) are *media perception* capabilities. **Autonomy's knowledge component (C15) does not exist yet.** Recommendation: rename the repository (e.g. `youtube-answers`) to free the name, keep it as application **P06**, and expose its transcript pipeline as job executors.

### 2.6 `talk`

A 60k-line Rust P2P messaging platform with its own identity, transport, trust engine and Android app. It solves a different, harder problem (decentralised human messaging under attack). Using it as the Autonomy bus would couple the platform to the least mature, most complex dependency. Keep independent (**P08**). Its Ed25519 identity and attestation ideas are consistent with C03; a later adapter could let Autonomy send owner notifications over TQVR as one more channel.

### 2.7 Media group: `youtube-processor`, `tts`, `video_reprocessor`, `yt_sync`, `knowledge`, `common_reflection`

Five repositories wrap `yt-dlp` and/or `ffmpeg`; four call a local model. Each is a working CLI or bot with its own config and state. They share no code.

- Reusable as **job executors** behind C07: `video.download`, `video.cut_compress`, `video.frames`, `vision.describe_frames`, `audio.transcribe`, `tts.synthesize`.
- `tts` depends on Coqui TTS 0.22 with Python ≤ 3.11 constraints [V]; upstream maintenance should be checked before building on it [A].
- `common_reflection` is a content repository (818 MB of assets); it should consume executors, not become one.

### 2.8 Independent products: `leadgen` (+`leadgen_front`), `wildwatch`, `gardening-service`, `my_fin_structure`, `tires`, `wires`

Each has its own users, auth, database and billing (where relevant). None depends on another. They are domain products; Autonomy should offer them services (jobs, model gateway, notifications, knowledge) without absorbing their logic. `my_fin_structure` handles private financial data and already uses only a local model for provider generation [V]; it must stay local-only.

### 2.9 Infrastructure and non-software

`my_vps` (WireGuard VPN scripts) overlaps with Tailscale, which every active edge design uses. `hw` is an unrelated bare-metal PoC. `book`, `business_ideas`, `basic-complexity` contain no software.

---

## 3. Cross-cutting findings

| Finding | Evidence | Consequence |
|---|---|---|
| **Four Telegram bots, four auth systems** | `edge_server`, `knowledge`, `leadgen`, `thequeue` bots; auth in `processor`, `leadgen`, `gardening-service`, `wildwatch` | Owner notifications and approvals should have one path (C05), not one per project. Customer-facing auth in products stays separate |
| **Five yt-dlp/ffmpeg wrappers, four Ollama clients, two LLM abstractions** | See §2.7; `jarvis` port vs direct SDK calls in `farm_agent`, `knowledge` | Shared executors (C07) and one model gateway (C12) remove duplication without forcing rewrites |
| **Two independently designed governance models** | `edge_server` T0–T3 tiers + TOTP approvals; `jarvis` read/write/sensitive tool permissions | Unify into one risk-class model (governance doc) that both map onto |
| **Two "control planes"** | `processor` control plane (jobs), `edge_server` fleet control plane (nodes) | They are complementary: jobs vs nodes. Keep both concepts, align their identity and envelope |
| **Good existing safety culture** | `edge_server` "dumb before smart", "doctor is never the patient", rate limits; `farm_automation` layered safety; `wildwatch` honest STATUS | Autonomy formalises these instead of inventing new ones |
| **Status documents that overstate** | `farm_agent` README vs code; several repos with `IMPLEMENTATION_COMPLETE.md` / "production-ready" | Component maturity in the catalog is based on code and tests, not READMEs |
| **No shared contracts at all** | No repo imports another; every interface is ad-hoc JSON | The first deliverable must be the contract package (C01) |
| **No real knowledge store, no policy engine, no unified audit, no tracing** | Absent in all repos | Gaps listed in [architecture.md](architecture.md#2-gap-analysis) |
