# External Integrations (World Adapters)

**Status:** Draft 0.1 · 2026-11-06

## 1. Adapter contract

Every adapter (`adapter:<system>@<cell>`) declares in its manifest:

```yaml
component_id: adapter:ha@field-station
observations:
  - stream: observation.ha.state_changed
    source: websocket.subscribe_events(state_changed)
    filter: {entities: "from farm/farm_core.yaml allowlist"}
operations:
  - name: ha.helper.set
    risk_class: R1
    params_schema: schemas/ha.helper.set.json
    idempotent: true
    cancel_safe: true
    read_state: ha.state.get        # used for reconciliation and verification
    expected_effect: state_equals
    simulate: false
  - name: opensprinkler.water_level.set
    risk_class: R2
    idempotent: true
    read_state: ha.state.get(sensor.opensprinkler_water_level)
    verification: {independent: "next-run flow volume vs expected (C14)", window: 36h}
    invariants: [range(30,200), max_step_per_day(25)]
  - name: ha.service.call
    risk_class: R3                   # generic service call is never exposed
credentials:
  read:  secret://field-station/ha_read_token
  write: secret://field-station/ha_write_token   # mounted only into C06
```

Rules: observations and operations are separate; generic "call anything" operations are R3 (never exposed); every write operation has `read_state` and an `expected_effect`.

## 2. Initial adapters

| Adapter | Observations | Operations (risk) | Protocol | Notes |
|---|---|---|---|---|
| **Home Assistant** | `state_changed` events, `get_states`, long-term statistics | helper set (R1), notification (R1), OpenSprinkler water level / rain delay via the farm restriction coordinator (R2, later), add-on restart (R1) | WebSocket API (`auth` → `subscribe_events`, `get_states`, `call_service`) [R]; REST for simple polling | Writes only through the farm design's helpers and coordinator, never raw valve or pump services. Respects farm D3: HA adjusts controller settings, never micromanages valves [V] |
| **Power** (Victron SmartShunt, Renogy, ECO-WORTHY) | battery SoC, voltage, current, PV | none | BLE plugins in `edge_server` [V], or HA entities | Feeds power stages |
| **Frigate / cameras** | detection events, snapshots | none initially | MQTT/HTTP (in `edge_server` [V]) | Reolink at Field Station via HA integration; `privacy: private` |
| **Telegram** | owner messages (trust `owner` for operators, `untrusted` otherwise) | send message (R1), approval prompts | Bot API (existing bots [V]) | Not end-to-end encrypted: no secrets [V] |
| **Relays** | relay state | power-cycle (R1, rate-limited, owner node only) | local HTTP API [V: edge_server] | One owner cell per relay |
| **Web fetch** | pages, feeds | none | HTTPS | Quarantined: output `trust: untrusted` |
| **YouTube / media** | transcripts, frames | none | yt-dlp, captions, Whisper (from `knowledge`, `youtube-processor` [V]) | Run as C07 jobs, untrusted |
| **Files** | documents in watched folders | write to designated output folders (R1) | Local FS on a cell | Path allowlists |
| **Email** (later) | inbox | send to owner (R1); send to others (R2) | IMAP/SMTP or provider API | Inbound mail is untrusted |
| **Google Sheets/Drive** (apps) | — | — | — | Stays inside `my_fin_structure`, `yt_sync`; not platform adapters |

## 3. Field Station specifics

The farm architecture already defines the layers that make this safe [V: farm_automation architecture v1.2]:
- Physical safety (interlocks, master valve, floats, independent runaway protection) works with HA, LAN and OpenSprinkler all failed.
- OpenSprinkler stored programs run without HA.
- HA owns coordination (restriction coordinator, climate factor, data-quality flags).

Autonomy sits **above** HA: it reads HA, writes only HA helpers that HA's own automations already validate and clamp, and later — after farm Phase 4 commissioning — the climate factor via the coordinator. Autonomy never writes OpenSprinkler or the RPS controller directly.

## 4. MCP and A2A

- **MCP** [R]: adapters may offer R0 tools as MCP servers; C06 offers R1+ tools as an MCP facade that returns `proposal_id`s. Target the 2026-07-28 revision (stateless, Tasks extension for long-running work) [R].
- **A2A** v1.0 [R]: deferred; boundary adapter only (contracts §13).

## 5. Voice and human interfaces

`jarvis` remains the voice product (P10). For Autonomy it is a C21 channel: speech → text → `jarvis` core → questions answered from C15/C09 read APIs; requests to act become proposals through C06's MCP facade. `jarvis` holds no device credentials.
