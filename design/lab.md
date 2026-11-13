# Autonomy lab: the whole system on a laptop

Status: **Accepted; built (processor `lab/`, edge_server `lab/`, `ops/lab/lab.sh`)** · 2026-11-11

## 1. Goal

Run the complete Autonomy implementation on one laptop and touch nothing real: no farm hardware, no cameras, no cloud, no language model download. One command in `autonomy` brings it up from the three local checkouts, after a `git pull` in each:

```
~/src/autonomy  ~/src/edge_server  ~/src/processor      (side by side)

cd ~/src/autonomy && ./ops/lab/lab.sh up
```

**Real:**
- the Telegram bot(s), using test bots you create with @BotFather;
- every Autonomy component, in the same code and containers as in production.

**Simulated:** everything else, in Docker containers.

## 2. What runs

| Production | In the lab | Owner (repo) |
|---|---|---|
| Edge server stack: Telegram bot, governance, `/ask` | Same bot image, built from your checkout, with **your real lab bot token** | edge_server |
| Frigate + IP cameras | Frigate with a light config, plus mock RTSP cameras (the existing `tests/` harness) | edge_server |
| Host helpers (systemd units that restart, reboot and redeploy) | `host-stub` container that answers the bot's request files. **restart** really restarts the lab container (`docker restart`). **reboot** and **redeploy** are simulated: it writes a result and advances a fake `/proc/uptime`, so verification has something to check. | edge_server |
| Home Assistant + farm sensors | Optional profile `farm`: HA + Mosquitto + the existing MQTT sensor simulator | edge_server |
| Job service control plane (on the edge server) | Same image, built from your checkout | processor |
| GPU worker + Ollama | Same worker image, built from your checkout, pointed at **`mock-llm`** | processor |
| A real model | **`mock-llm`**: a small container that speaks Ollama `/api/chat` and OpenAI `/v1/chat/completions` and answers from a rules file. It downloads no model. | processor |
| Tailscale network | One Docker network, `autonomy-lab` | autonomy |
| Owner keys, policy bundle, grants | Lab-only keys and a signed lab bundle (for example an A1 grant for `agent:node-assistant`), made with autonomy-core | autonomy |
| Your authenticator app | A lab-only TOTP secret. `lab.sh code` prints the current code, and the secret can also be added to an authenticator app. | autonomy |

Nothing in the lab can reach the farm or a cloud model:
- The model routing contains only `mock-llm`.
- `host-stub` acts only on lab containers (a label filter on the Docker socket).
- Containers that need no internet run on a network without outbound access. The bot and `mock-llm`'s image build are the exceptions; the bot needs api.telegram.org.

## 3. One orchestrator, three lab scripts

```
autonomy/ops/lab/lab.sh            orchestrator: order, shared state, keys, policy, checks
processor/lab/lab.sh               processor parts: control plane, worker, mock-llm
edge_server/lab/lab.sh             edge parts: bot, frigate + cameras, host-stub, (farm)
```

Each repo owns its lab part and can run it alone, which helps when you work in one repo. The orchestrator only calls them through a small contract.

### 3.1 The contract every repo lab script follows

```
lab/lab.sh up | down | status | logs [service] | reset
```

Inputs, from environment variables set by the orchestrator, with defaults for running alone:

| Variable | Meaning |
|---|---|
| `LAB_DIR` | Shared state directory (default `~/.autonomy-lab`); each repo uses `$LAB_DIR/<repo>/` |
| `LAB_NETWORK` | Docker network to join (default `autonomy-lab`; created if missing) |
| `LAB_PROJECT` | Compose project prefix (default `alab`), so `down` removes only lab containers |
| `LAB_REPOS` | Parent directory of the three checkouts (default: the directory above this repo) |

**Outputs:** each script writes `$LAB_DIR/<repo>.env` with what other parts need. For example, processor writes `PROCESSOR_URL`, `PROCESSOR_API_KEY_FILE` and `MOCK_LLM_URL`. Scripts read the `.env` files of the parts they depend on, and fail with a clear message if one is missing.

**Rules for the scripts:**
- They need only `bash`, `git`, `docker` and `docker compose` on the host. Anything else (key generation, signing, YAML) runs in a throwaway container, so macOS bash 3.2 and BSD `sed` don't matter.
- They are idempotent: `up` twice changes nothing, and secrets are made once and kept.
- They never use `sudo`, never change the host's network, firewall or systemd, and never run the production deploy steps.

### 3.2 Order of `autonomy/ops/lab/lab.sh up`

1. **Check:** Docker is running, the three repos are present, and each repo's `lab/lab.sh` exists. Show each repo's branch and commit, so you know what you are testing.
2. **Secrets, asked once, kept in `$LAB_DIR/secrets.env` (mode 600, never in a repo):**
   - your lab bot token(s) and your Telegram user id;
   - a generated lab TOTP secret;
   - generated lab owner keys.
3. **Autonomy lab authority** (a container with autonomy-core from your checkout):
   - signs a lab registry (lab cell `edge-lab`; owner keys);
   - signs a lab policy bundle with grants for the lab agent, A1 by default, so its proposals go to `/pending`;
   - writes the model routing (`mock-llm` only, locality `owned`).
4. `processor/lab/lab.sh up`:
   - builds the images from the checkout;
   - starts `mock-llm`, the control plane and the worker;
   - registers the worker's Ed25519 key;
   - creates an API key for the bot;
   - writes `processor.env`.
5. `edge_server/lab/lab.sh up`:
   - **compose generation:** runs the real `deploy-edge-server.sh` compose and config generation (`prepare_*`, `create_app_stack`) inside a throwaway Ubuntu container, with `--lab` settings: lab directories; no packages, firewall, Tailscale or systemd; host helpers replaced by `host-stub`. The lab therefore checks the compose file production would get.
   - **bot image:** built with autonomy-core **from your local checkout**. This needs a small Dockerfile change: an optional `--build-context autonomy=...`. Production keeps the pinned commit.
   - starts Frigate with the mock cameras, the bot, `host-stub`, and the `farm` profile if asked;
   - writes `edge_server.env`.
6. **Smoke checks** (automatic):
   - each component's health;
   - the bot's `getMe` (the token works);
   - one `llm.chat` job through processor to `mock-llm`;
   - the bot's status file shows governance and `/ask` on;
   - the audit log verifies.
7. **Print what to do next:** the bot's name, `lab.sh code`, and the guided scenarios (§5).

`down` runs the same steps in reverse; data is kept. `reset` also deletes `$LAB_DIR` apart from `secrets.env`. `up --only processor` (or `edge_server`) brings up one part.

### 3.3 Which code gets tested

By default, **your three working copies, including changes you haven't committed**. The pins in edge_server (autonomy-core commit, processor image tag) are ignored. `lab.sh up --pinned` uses the pins instead, to try exactly what production would deploy.

## 4. `mock-llm`

- A tiny Python web server with no model. It answers from `rules.yaml`: a list of `{match: <words in the last user message or system prompt>, answer: <text or JSON>}`, plus defaults for the task classes in use:
  - the node assistant returns `{"answer": …, "restart": ""}`, or `"frigate"` when the question mentions stuck or no detections;
  - the reviewer returns `{"verdict": "ok", …}`, or `"escalate"` for `/reboot`.
- It accepts `format`, a JSON schema, and returns valid JSON for it. Answers say they come from the mock (`model: mock-llm`).
- An admin endpoint switches modes, through `lab.sh llm <mode>`:
  - `normal`;
  - `down` (refuses connections);
  - `slow` (answers after N s, to test deadlines);
  - `garbage` (not JSON, to test the retry and escalation);
  - `echo` (repeats the prompt, to see exactly what a model would get; the lab only).
- It logs every request it receives into `$LAB_DIR/processor/mock-llm.jsonl`, so you can check what data would have left the edge server.

## 5. Scenarios (guided, with real Telegram)

`lab.sh scenario <name>` sets the scene, tells you what to send to your lab bot, then watches the audit log, the job queue and the mock-llm log, and reports PASS or FAIL for each expectation.

| Scenario | You send | The lab checks |
|---|---|---|
| `status` | `/status` | Status includes the processor queue, workers online and cameras |
| `ask` | `/ask how is the node?` | `llm.chat` job → mock-llm received only site data → answer in Telegram → `agent.started`/`agent.finished` audited |
| `ask-restart` | `/ask why no detections?`, then `/approve <id> <code>` | The proposal goes to `/pending` (A1 grant). After the approval, host-stub restarts the lab Frigate and verification reports `confirmed` |
| `owner-restart` | `/restart frigate <code>` | Evaluated → decided → started → succeeded → verified, in **one trace** (IT-71) |
| `contradicted` | `/restart frigate <code>` with host-stub in "lie" mode (reports ok, restarts nothing) | Verification `contradicted`, `incident.opened`, a warning in Telegram (ST-12 on this node) |
| `gpu-asleep` | `lab.sh worker stop`, then `/ask …` | "No model answered"; after 30 min (shortened in the lab) the watcher alerts that jobs are waiting with no worker |
| `model-down-review` | `lab.sh llm down`, `MODEL_REVIEW=on`, `/restart frigate <code>` | The review is recorded as *skipped* and the restart proceeds (ST-03) |
| `suspend` | `/suspend agent:node-assistant`, then `/ask …` | Refused as suspended; its waiting proposals are withdrawn |
| `reboot` | `/reboot <code>` | Simulated reboot: the fake uptime is reset, the post-reboot notice is sent, and verification by boot time reports `confirmed` |
| `farm` (profile) | `/status` | HA and the sensor simulator are up, ready for M10, which is not implemented yet |

`lab.sh verify` runs every check that needs no Telegram message (§3.2 step 6, plus the job, model and governance paths through the components' own APIs). That is what CI runs. Scenarios that need you to send a message are manual by design: the lab uses your real bot, and a bot cannot send messages as you.

## 6. Resources and platforms

- **Platforms:** macOS (the owner's laptop: Docker Desktop or OrbStack) and Linux (CI). Apple silicon builds arm64 images locally; processor's published images are amd64 only, which is another reason to build locally.
- **Memory:** Frigate about 1 GB, the processor parts under 100 MB, the bot about 100 MB, mock-llm about 50 MB. The `farm` profile adds about 700 MB (Home Assistant). Total about 1.3 GB, or about 2 GB with `farm`.
- **First run** about 5 minutes (image builds); later runs under a minute.

## 7. Changes per repo

| Repo | New | Changed |
|---|---|---|
| autonomy | `ops/lab/lab.sh`; `ops/lab/authority/` (keys, registry, bundle, routing, through autonomy-core in a container); `ops/lab/scenarios/`; this design; a CI job `lab verify` (Linux, with a mocked Telegram API, because CI has no bot token) | — |
| processor | `lab/lab.sh`; `lab/compose.yml`; `lab/mock-llm/` (server, rules, Dockerfile) | — |
| edge_server | `lab/lab.sh`; `lab/compose.lab.yml` (cameras, host-stub, farm profile); `lab/host-stub/` | `deploy-edge-server.sh`: a `--lab` mode for the compose and config generation only. `telegram-bot/Dockerfile`: optional local autonomy-core build context. The bot: `HOST_PROC`-style overrides already exist; none expected |

## 8. Safety rules for the lab

- **No production secrets.** The lab uses its own bot, its own TOTP secret, its own owner keys and its own API keys. The lab bot token stays in `$LAB_DIR/secrets.env`.
- **Nothing real can be controlled.** host-stub acts only on containers labelled `autonomy.lab=true`, and the lab never mounts the host's `/proc` or the requests directory of a real deployment.
- **No cloud models.** The lab's routing file contains only `mock-llm`; `lab.sh verify` fails if any other endpoint appears.
- **`lab.sh down` never touches containers outside `LAB_PROJECT`.**

## 9. Decisions (owner, 2026-11-11)

1. **Laptop: macOS** (Docker Desktop or OrbStack; Intel or Apple silicon). Documentation and defaults target macOS. The scripts keep the portable-bash rule, so Linux works too (it is what CI runs).
2. **One edge node only.** One lab bot; no `--nodes` option. Fleet testing stays in edge_server's unit tests.
3. **`farm` profile off by default**; `lab.sh up --farm` turns it on.
4. **CI:** `lab verify` on pull requests in autonomy, nightly in processor and edge_server.

## 10. Build order once approved

1. processor: `lab/` with mock-llm. Can be tested alone: job → mock-llm → result.
2. edge_server: `--lab` compose mode, host-stub, `lab/lab.sh`. Can be tested alone with a processor lab running.
3. autonomy: the orchestrator, lab authority (keys, bundle, routing), smoke checks, scenarios, CI `lab verify`.
4. Documentation: a "Lab" section in each README; this document becomes *Accepted*.

## 11. As built: where the lab differs from this design

- **Frigate is a stand-in** (`edge_server/lab/frigate-stub`), not the real image (about 1 GB, needs video and a GPU/CPU detector). It serves what the bot reads (`/api/version`, `/api/stats`, `/api/config`), its uptime resets when its container restarts, and `lab.sh cam <name> <fps>` makes a camera lose video. A real-Frigate option can follow.
- **Compose is rendered by the production functions** (`prepare_telegram_bot`, `create_app_stack`) in a throwaway Ubuntu container with `docker`, `systemctl`, `tailscale`, `iptables` and `ufw` as no-op stubs, then adjusted by `compose.lab.yml` (lab network, stand-ins, no privileged mode or devices). The production `Dockerfile` is unchanged: the lab image is it plus `autonomy-core` from the local checkout (`lab/bot.Dockerfile`, a named build context).
- **`host-stub` runs the real `service-request.py`** for `/restart` and `/logs`. `/reboot` and `/redeploy` are simulated: a fake `/proc/uptime` the bot reads (`HOST_PROC`), reset on "reboot", refused within 10 minutes of the last one, like the production unit.
- **`mock-llm` rules are JSON** (`rules.json`), not YAML, to keep it standard-library only. Modes: normal, slow, garbage, echo, error; `down` stops the container. Every request is logged in full.
- **(Superseded in the lab authority change: the lab now makes an authority and an A1 grant; `up --baseline` gives the behaviour described here.) No lab authority, no A1 grant.** A signed policy bundle needs a registry on the node, which a one-node install does not have (the bot runs the baseline policy), so the lab runs the baseline: `/ask` can suggest a restart but not run it; you run `/restart` with a code. Granting the agent A1 needs registry support on single nodes (later). Model routing is the default one from the job service URL, which contains only the processor endpoint, so nothing can reach a cloud model; `lab.sh check` also fails if a cloud key is in the bot's environment.
- **Mocked Telegram** (`edge_server/lab/telegram-mock`) for CI and for runs without a bot token; it forgets acknowledged updates like the real API.
- **Not built yet:** the `farm` profile, `--only` combined with checks beyond each part's own, real Frigate.
- **CI:** processor is private, so autonomy's lab job needs a repository secret `LAB_REPOS_TOKEN` (read access to realsystem/processor); without it the job is skipped. edge_server and processor run their own parts nightly.
- **What the lab found already:** the deploy script did not install `assistant.py` (so `/ask` was off on a deployed node). Fixed in edge_server, with a test.
