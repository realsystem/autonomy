# The lab

The whole implementation on your laptop, touching nothing real: no farm hardware, no cameras, no cloud, no language model. Design: [design/lab.md](../../design/lab.md).

```
~/src/autonomy  ~/src/edge_server  ~/src/processor      (side by side; git pull in each)

cd ~/src/autonomy && ops/lab/lab.sh up
```

It builds from the three working copies (uncommitted changes included), so what you test is what is on disk.

## What you need

- Docker Desktop or OrbStack (Compose 2.24 or newer), `git`, `bash`. Nothing else is installed on the Mac.
- For a **real Telegram bot**: a *test* bot from @BotFather (not your production bot: only one program can poll a bot at a time) and your Telegram user id. The script asks once and keeps them in `~/.autonomy-lab/secrets.env` (mode 600).
- Or use `--mock-telegram`: no token, a fake Telegram API in a container, and `lab.sh say` / `replies` to talk to the bot.

## Run it

```bash
ops/lab/lab.sh up                    # your own test bot
ops/lab/lab.sh up --mock-telegram    # no bot needed
ops/lab/lab.sh verify                # every scenario, automatically (mock Telegram)
ops/lab/lab.sh scenarios             # what to try by hand
ops/lab/lab.sh code                  # the current code for /restart and /reboot
ops/lab/lab.sh llm error             # the fake model: normal | down | slow [s] | garbage | echo | error
ops/lab/lab.sh down                  # stop, keep data
ops/lab/lab.sh reset                 # remove containers, volumes and lab state (keeps your token)
```

`up --only processor` or `--only edge_server` starts one part. `--pinned` builds the bot with the pinned `autonomy-core` instead of your checkout. The farm profile (`--farm`) is not built yet.

## What runs

| Production | In the lab |
|---|---|
| Edge server bot and governance | the real bot, built from your edge_server and autonomy checkouts |
| Frigate and cameras | `frigate-stub` (Frigate's API as the bot reads it; a camera can lose video) |
| Host helpers (restart, reboot, redeploy) | `host-stub`: the real restart/logs helper; reboot and redeploy are simulated |
| Job service and GPU worker | the real control plane and worker, built from your processor checkout |
| The model | `mock-llm`: no model, answers from rules, logs every request in full |
| Telegram | yours (a test bot) or `telegram-mock` |

Everything is in one Docker network, `autonomy-lab`; containers are labelled `autonomy.lab`. The model routing contains only the job service, and `lab.sh check` fails if a cloud key is in the bot's environment. State lives in `~/.autonomy-lab`; what a model would have been sent is in `~/.autonomy-lab/processor/mock-llm/mock-llm.jsonl`.

## `verify`

Runs against the mocked Telegram API: `/status`; `/ask` through the whole model path; the model suggesting a restart and `/restart frigate` with the lab code (the real helper restarts the container); the model failing (`/ask` says so); `/audit`; a simulated `/reboot` (the bot comes back and says so); and a check that no secret (bot token, authenticator secret, job service key) reached the model log. About 5 minutes.

CI runs it on pull requests in this repo (free: this repository and processor are public), with no secret. farm_automation is private, so the nightly `--farm-config` job needs a repository secret `LAB_REPOS_TOKEN`: a fine-grained personal access token, resource owner `realsystem`, repository `farm_automation`, permission "Contents: read" only (GitHub: Settings, Developer settings, Personal access tokens, Fine-grained tokens; then this repository's Settings, Secrets and variables, Actions). Without it that job is skipped. The full lab (stand-in Home Assistant) runs on lab and core changes; the whole stack with real Home Assistant and the farm configuration (`--farm-config`) runs nightly and on demand (Actions, Lab, Run workflow).

## Farm profile (`--farm`)

`ops/lab/lab.sh up --mock-telegram --farm` adds a Home Assistant stand-in (`farm/ha-stub`) with simulated
Ecowitt **GW3000** gateway, **WS90** weather station and **WH51** soil sensors, plus the read-only
**Field Station cell** (M10) built from this checkout. The stub also holds decoy entities (a lock, an alarm
panel, a phone location) that are not on the allowlist, and refuses and records any write attempt.

    ops/lab/lab.sh farm events 20                         what the cell recorded
    ops/lab/lab.sh farm set sensor.ws90_temperature 35    change a sensor value
    ops/lab/lab.sh farm restart-ha 10                     Home Assistant goes away; the cell reconnects
    ops/lab/lab.sh farm writes                            every write attempt (must stay empty)
    /farm  (in Telegram)                                  the latest readings, read by the bot from the cell's status file

`verify` adds six scenarios when the profile is up: farm-observe, farm-change, farm-ha-restart,
farm-heartbeat (memory under 300 MB), farm-no-writes and farm-bot (`/farm` in Telegram shows the readings
and nothing off the allowlist; needs `up --farm`, not `--only farm`). The same cell runs on a real Home Assistant as the
add-on in `ops/ha-addon/autonomy-station/`.

### With the farm's own configuration (`--farm-config`, the fourth repository)

`ops/lab/lab.sh up --mock-telegram --farm-config` needs a checkout of `realsystem/farm_automation` next to the
others (`LAB_REPOS/farm_automation`) and replaces the stand-in with a **real Home Assistant** (`ghcr.io/home-assistant/home-assistant`,
about 700 MB) that loads that repository's `homeassistant/` packages. The lab gets a token through Home Assistant's
onboarding API, seeds the raw soil entities (`sensor.wh51_ch*_soil_moisture`, as a gateway integration would),
and starts the cell with the allowlist printed by the repository's own `tools/farm.py allowlist`, so the list can't
drift from `farm/farm_core.yaml`. `farm set sensor.wh51_ch3_soil_moisture 140` then shows the package's validity rule at
work (the `farm_soil_*` sensors go unavailable), and `/farm` shows what Home Assistant computed.

`verify` runs farm-config-observe, farm-config-invalid, farm-config-recover, farm-config-allowlist and farm-config-bot
instead of the stand-in's scenarios. Each repository stays usable alone: without the flag the lab never touches
farm_automation, and farm_automation needs nothing from the lab. Not covered: the Ecowitt integration itself (the lab
writes the raw states directly), OpenSprinkler, and the stand-in's write log (the cell is read only by construction).
Status: written without a Docker daemon, so the first run on a laptop is its first test.

## The owner's authority (A1 grant)

`up` also makes a lab authority (`ops/lab/authority/`): lab-only owner keys, a signed registry and a signed
policy bundle, in `~/.autonomy-lab/autonomy/`. The bundle grants `agent:node-assistant` level **A1** on
`host.restart_service` for `frigate`. So when `/ask` concludes that a restart helps, the proposal is permitted
by policy and then **waits for you**: `/pending` lists it, `/approve <id> <code>` runs it through the Action
Gateway (real restart in the lab), and `/audit` shows the whole story. The `ask-grant` scenario in `verify`
does exactly that.

`up --baseline` skips the authority: the node runs the baseline policy, `/ask` only suggests a restart, and you
run `/restart frigate <code>` yourself (the `ask-restart` scenario). Keys never leave the lab directory;
`reset` removes them.
