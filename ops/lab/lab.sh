#!/usr/bin/env bash
# One script for the whole lab (design/lab.md): the job processor with a fake model
# (processor/lab), the edge node with stand-ins for Frigate and the host helpers
# (edge_server/lab), and a real or mocked Telegram. No real model, no hardware, no cloud.
#
#   ops/lab/lab.sh up [--mock-telegram] [--pinned] [--farm] [--baseline] [--only processor|edge_server|farm]
#   ops/lab/lab.sh down | status | logs [service] | reset | check
#   ops/lab/lab.sh verify                 all scenarios, automatically (needs --mock-telegram)
#   ops/lab/lab.sh scenarios              what to try by hand
#   ops/lab/lab.sh code                   the current /restart and /reboot code
#   ops/lab/lab.sh llm normal|down|slow [s]|garbage|echo|error      the fake model's mode
#   ops/lab/lab.sh say "/ask ..." | replies [n]                     (mock Telegram)
#   ops/lab/lab.sh farm events [n] | set <entity> <state> | restart-ha [s]   (with --farm)
#
# Run after `git pull` in processor, edge_server and autonomy: it builds from the working
# copies (uncommitted changes included). Needs only bash, git, docker and docker compose
# 2.24+ on the host; works with macOS bash 3.2.
# Environment (optional): LAB_DIR (~/.autonomy-lab), LAB_REPOS (parent of the three
# checkouts), LAB_NETWORK, LAB_PROJECT, LAB_BOT_TOKEN, LAB_TELEGRAM_ID.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AUTONOMY="$(cd "$HERE/../.." && pwd)"
export LAB_REPOS="${LAB_REPOS:-$(cd "$AUTONOMY/.." && pwd)}"
export LAB_DIR="${LAB_DIR:-$HOME/.autonomy-lab}"
export LAB_NETWORK="${LAB_NETWORK:-autonomy-lab}"
export LAB_PROJECT="${LAB_PROJECT:-alab}"
export LAB_AUTONOMY_DIR="$AUTONOMY"
PROC="$LAB_REPOS/processor"
EDGE="$LAB_REPOS/edge_server"
SECRETS="$LAB_DIR/secrets.env"
MODE_FILE="$LAB_DIR/orchestrator.mode"
FARM_FILE="$LAB_DIR/orchestrator.farm"
FARM="$HERE/farm"
export LAB_FARM_STATE="$LAB_DIR/farm"

die() { echo "lab: $*" >&2; exit 1; }
say() { echo "lab: $*"; }
secret() { { sed -n "s/^$1=//p" "$SECRETS" 2>/dev/null || true; } | tail -n 1; }

preflight() {
  command -v docker >/dev/null || die "docker is not installed"
  docker info >/dev/null 2>&1 || die "docker is not running (start Docker Desktop)"
  local name dir
  for name in processor edge_server autonomy; do
    if [ "${LAB_ONLY:-}" = farm ] && [ "$name" != autonomy ]; then continue; fi
    case "$name" in processor) dir="$PROC" ;; edge_server) dir="$EDGE" ;; *) dir="$AUTONOMY" ;; esac
    [ -d "$dir/.git" ] || die "no $name checkout at $dir (LAB_REPOS=$LAB_REPOS)"
    [ "$name" = autonomy ] || [ -x "$dir/lab/lab.sh" ] || die "$name has no lab/lab.sh: git pull in $dir (the lab needs its latest main)"
    printf '  %-12s %-22s %s%s\n' "$name" "$(git -C "$dir" rev-parse --abbrev-ref HEAD)" \
      "$(git -C "$dir" rev-parse --short HEAD)" \
      "$([ -n "$(git -C "$dir" status --porcelain 2>/dev/null)" ] && echo ' (uncommitted changes included)')"
  done
}

mock_on() { [ "$(cat "$MODE_FILE" 2>/dev/null)" = "mock" ]; }
grant_on() { [ -s "$LAB_DIR/autonomy/policy-bundle.json" ]; }

authority() { # owner keys, a signed registry and policy bundle with an A1 grant for the node assistant
  mkdir -p "$LAB_DIR/autonomy"
  docker build -q -t alab-authority:lab -f "$HERE/authority/Dockerfile" "$AUTONOMY" >/dev/null
  docker run --rm --user "$(id -u):$(id -g)" -v "$LAB_DIR/autonomy:/out" alab-authority:lab /out | sed 's/^/  /'
}

farm_on() { [ -f "$FARM_FILE" ]; }

farmc() { LAB_STATE="$LAB_FARM_STATE" docker compose -f "$FARM/compose.yml" "$@"; }

ha_admin() { # METHOD PATH [json]: the Home Assistant stand-in's admin API
  docker exec "${LAB_PROJECT}-ha" python -c '
import sys, urllib.request
m, p, b = sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None
r = urllib.request.Request("http://127.0.0.1:8123" + p, b.encode() if b else None, {"Content-Type": "application/json"}, method=m)
print(urllib.request.urlopen(r, timeout=30).read().decode())' "$@"
}

station_events() { # n [kind]
  docker exec "${LAB_PROJECT}-station" autonomy station events --db /data/station-events.db -n "$1" ${2:+--kind "$2"}
}

farm_up() {
  docker network inspect "$LAB_NETWORK" >/dev/null 2>&1 || docker network create "$LAB_NETWORK" >/dev/null
  mkdir -p "$LAB_FARM_STATE/config" "$LAB_FARM_STATE/status"
  chmod 777 "$LAB_FARM_STATE/status" # the cell (uid 1000) writes status.json here; the bot reads it
  cp "$FARM/station.json" "$LAB_FARM_STATE/config/station.json"
  farmc up -d --build --wait
  touch "$FARM_FILE"
}

farm_check() {
  local out
  for _ in $(seq 1 20); do
    out="$(station_events 200 2>/dev/null || true)"
    if grep -q "observation.state" <<< "$out" && grep -q "cell.heartbeat" <<< "$out"; then
      say "farm check ok: the Field Station cell is observing Home Assistant (read only)"
      return 0
    fi
    sleep 2
  done
  die "the Field Station cell recorded nothing: $(farmc logs --no-color --tail 20 station 2>&1 | tail -20)"
}

tg() { # METHOD PATH [json]: the mocked Telegram API
  docker exec telegram-mock python -c '
import sys, urllib.request
m, p, b = sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None
r = urllib.request.Request("http://127.0.0.1:8081" + p, b.encode() if b else None, {"Content-Type": "application/json"}, method=m)
print(urllib.request.urlopen(r, timeout=30).read().decode())' "$@"
}

cmd_up() {
  local only="" edge_args=() proc_args=() a farm=0 baseline=0
  while [ $# -gt 0 ]; do
    a="$1"; shift
    case "$a" in
      --mock-telegram) edge_args+=("$a") ;;
      --pinned) edge_args+=("$a") ;;
      --only) only="${1:-}"; shift || true ;;
      --farm) farm=1 ;;
      --baseline) baseline=1 ;; # no owner authority: the baseline policy, so /ask can only suggest
      *) die "unknown option $a" ;;
    esac
  done
  case "$only" in ""|processor|edge_server|farm) ;; *) die "--only is processor, edge_server or farm" ;; esac
  [ "$only" = farm ] && farm=1
  say "what will be built:"
  LAB_ONLY="$only" preflight
  mkdir -p "$LAB_DIR"
  if [ "$only" != farm ]; then
    case " ${edge_args[*]:-} " in *" --mock-telegram "*) echo mock > "$MODE_FILE" ;; *) echo real > "$MODE_FILE" ;; esac
  fi
  [ "$farm" = 1 ] || rm -f "$FARM_FILE"

  if [ "$farm" = 1 ]; then # the bot's /farm reads the cell's status file from this directory
    mkdir -p "$LAB_FARM_STATE/status"; chmod 777 "$LAB_FARM_STATE/status"
    export LAB_FARM=1 LAB_FARM_STATUS_DIR="$LAB_FARM_STATE/status"
  fi
  if [ -z "$only" ] || [ "$only" = edge_server ]; then
    if [ "$baseline" = 1 ]; then rm -rf "$LAB_DIR/autonomy/policy-bundle.json" "$LAB_DIR/autonomy/registry"; else authority; fi
  fi
  if [ -z "$only" ] || [ "$only" = processor ]; then
    "$PROC/lab/lab.sh" up ${proc_args[@]+"${proc_args[@]}"}
  fi
  if [ -z "$only" ] || [ "$only" = edge_server ]; then
    "$EDGE/lab/lab.sh" up ${edge_args[@]+"${edge_args[@]}"}
  fi
  if [ "$farm" = 1 ]; then farm_up; farm_check; fi
  [ -n "$only" ] || cmd_check
  if [ "$only" = farm ]; then
    say "farm up. Next: ops/lab/lab.sh farm events 20   or   ops/lab/lab.sh verify farm"
  elif mock_on; then
    say "up (mock Telegram). Next: ops/lab/lab.sh verify, or  ops/lab/lab.sh say \"/ask how is the node?\"  then  replies"
  else
    say "up. In Telegram, message your lab bot (it answers only user id $(secret LAB_TELEGRAM_ID)):"
    say "  /status    /ask how is the node?    /restart frigate $(printf '<code>')    (ops/lab/lab.sh code)"
    say "More to try: ops/lab/lab.sh scenarios"
  fi
}

# the parts together: the model path and nothing leaving the lab
cmd_check() {
  "$PROC/lab/lab.sh" check
  "$EDGE/lab/lab.sh" check
  local nets
  nets="$(docker inspect -f '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}' telegram-bot frigate "${LAB_PROJECT}-worker" "${LAB_PROJECT}-mock-llm" 2>/dev/null | tr -s ' \n' ' ')"
  say "network check: lab containers are on: $nets"
  if docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' telegram-bot | grep -qiE '^(ANTHROPIC|OPENAI)_API_KEY='; then
    die "the bot has a cloud model key in its environment: not a lab"
  fi
  if farm_on; then farm_check; fi
  say "check ok"
}

cmd_down() {
  if farm_on; then farmc down --remove-orphans || true; rm -f "$FARM_FILE"; fi
  [ -x "$EDGE/lab/lab.sh" ] && "$EDGE/lab/lab.sh" down || true
  [ -x "$PROC/lab/lab.sh" ] && "$PROC/lab/lab.sh" down || true
  rm -f "$MODE_FILE"
}

cmd_reset() {
  if [ -f "$FARM/compose.yml" ]; then LAB_STATE="$LAB_FARM_STATE" farmc down -v --remove-orphans >/dev/null 2>&1 || true; fi
  rm -rf "$LAB_FARM_STATE" "$FARM_FILE" "$LAB_DIR/autonomy"
  [ -x "$EDGE/lab/lab.sh" ] && "$EDGE/lab/lab.sh" reset || true
  [ -x "$PROC/lab/lab.sh" ] && "$PROC/lab/lab.sh" reset || true
  docker network rm "$LAB_NETWORK" >/dev/null 2>&1 || true
  rm -f "$MODE_FILE" "$LAB_DIR"/*.env.bak
  say "reset: containers, volumes and lab state removed. Kept: $SECRETS (your lab token and TOTP secret)"
}

cmd_status() { "$PROC/lab/lab.sh" status || true; "$EDGE/lab/lab.sh" status || true; if farm_on; then farmc ps || true; fi; }
cmd_logs() { # service: the right part's logs
  case "${1:-}" in
    mock-llm|control-plane|worker) "$PROC/lab/lab.sh" logs "$@" ;;
    ha|station) farmc logs --no-color --tail 100 "$@" ;;
    "") "$PROC/lab/lab.sh" logs | tail -40; "$EDGE/lab/lab.sh" logs | tail -80 ;;
    *) "$EDGE/lab/lab.sh" logs "$@" ;;
  esac
}

cmd_scenarios() {
  cat <<'TXT'
Things to try by hand (with your lab bot in Telegram; or `ops/lab/lab.sh say "..."` + `replies` with --mock-telegram)

  status        /status                        the node, Frigate (stand-in) and the job service
  ask           /ask how is the node?          one model call: bot -> job service -> worker -> mock-llm
  ask-restart   /ask frigate looks stuck       the model suggests a restart; you approve or run it
                /restart frigate <code>        <code> from `ops/lab/lab.sh code`
  camera        ops/lab/lab.sh cam cam1_front 0   then /status: NO VIDEO (edge_server/lab/lab.sh cam)
  model-down    ops/lab/lab.sh llm error       then /ask ...: "No model answered";  llm normal  to restore
  slow          ops/lab/lab.sh llm slow 20     /ask waits; try the 3-minute limit with `llm slow 200`
  echo          ops/lab/lab.sh llm echo        then /ask ...: the answer shows exactly what the model was sent
  reboot        /reboot <code>                 simulated: the bot and Frigate restart, "Back online after reboot"
  audit         /audit                         the signed action log
  farm          ops/lab/lab.sh up --farm       adds Home Assistant (stand-in, simulated GW3000/WS90/WH51) and the read-only Field Station cell
                ops/lab/lab.sh farm events 20  what the cell recorded (observations, heartbeats)
                ops/lab/lab.sh farm set sensor.ws90_temperature 35   a change shows up as an event
                ops/lab/lab.sh farm restart-ha 10   Home Assistant goes away; the cell reconnects
  what left     tail -f ~/.autonomy-lab/processor/mock-llm/mock-llm.jsonl     every model request, in full
TXT
}

cmd_llm() { "$PROC/lab/lab.sh" llm "$@"; }
cmd_code() { "$EDGE/lab/lab.sh" code; }
cmd_say() { "$EDGE/lab/lab.sh" say "$@"; }
cmd_replies() { "$EDGE/lab/lab.sh" replies "$@"; }
cmd_farm() {
  farm_on || die "the farm profile is not up: ops/lab/lab.sh up --farm"
  case "${1:-}" in
    events) station_events "${2:-20}" ${3:+"$3"} ;;
    set) [ $# -ge 3 ] || die "usage: lab.sh farm set <entity> <state>"
         ha_admin POST /_lab/set "{\"entity_id\":\"$2\",\"state\":\"$3\"}" ;;
    restart-ha) ha_admin POST /_lab/restart "{\"seconds\":${2:-10}}" ;;
    writes) ha_admin GET /_lab/writes ;;
    *) die "usage: lab.sh farm events [n] [kind] | set <entity> <state> | restart-ha [seconds] | writes" ;;
  esac
}

cmd_cam() { "$EDGE/lab/lab.sh" cam "$@"; }

# ---- verify: the scenarios, automatically (mocked Telegram) -------------------------------

LAST_CODE=""
fresh_code() { # a code the bot has not seen: wait for the next 30 s window if needed
  local c
  for _ in $(seq 1 20); do
    c="$(cmd_code)"
    [ "$c" != "$LAST_CODE" ] && { LAST_CODE="$c"; echo "$c"; return 0; }
    sleep 2
  done
  die "no fresh code"
}

wait_reply() { # pattern (lower case, | for alternatives) timeout_s
  local pat="$1" t="${2:-60}" out
  for _ in $(seq 1 $((t / 2))); do
    out="$(tg GET /_lab/sent | tr '[:upper:]' '[:lower:]')"
    if grep -qE "$pat" <<< "$out"; then return 0; fi
    sleep 2
  done
  echo "--- no reply matching '$pat' in $t s. The bot said:" >&2
  "$EDGE/lab/lab.sh" replies 6 >&2 || true
  echo "--- bot log:" >&2; docker logs --tail 25 telegram-bot >&2 2>&1 || true
  echo "--- job service log (jobs):" >&2; docker logs --tail 400 "${LAB_PROJECT}-processor" 2>&1 | grep -v "worker/poll\|/health\|/stats" | tail -15 >&2 || true
  echo "--- worker log:" >&2; docker logs --tail 15 "${LAB_PROJECT}-worker" >&2 2>&1 || true
  return 1
}

step() { printf 'verify: %-12s' "$1"; }
ok() { echo "ok"; }

wait_events() { # kind pattern timeout_s: until an event of that kind matches the pattern
  local kind="$1" pat="$2" t="${3:-40}" out
  for _ in $(seq 1 $((t / 2))); do
    out="$(station_events 400 "$kind" 2>/dev/null || true)"
    if grep -qE "$pat" <<< "$out"; then return 0; fi
    sleep 2
  done
  echo "--- no $kind event matching '$pat' in $t s. Last events:" >&2
  station_events 8 >&2 || true
  return 1
}

count_events() { station_events 5000 "$1" | wc -l | tr -d ' '; }

verify_farm() {
  local before out rss
  step farm-observe
  out="$(station_events 5000 observation.state)"
  grep -q "sensor.ws90_temperature" <<< "$out" || die "no observation of the weather station"
  grep -q "sensor.wh51_ch1_soil_moisture" <<< "$out" || die "no observation of the soil sensor"
  if grep -qE "lock.front_door|alarm_control_panel|owner_phone|secret_api_notes" <<< "$out"; then
    die "the cell recorded an entity that is not on its allowlist"
  fi
  ok

  step farm-change
  ha_admin POST /_lab/set '{"entity_id":"sensor.ws90_temperature","state":"33.3"}' >/dev/null
  ha_admin POST /_lab/set '{"entity_id":"lock.front_door","state":"unlocked"}' >/dev/null
  wait_events observation.state 'sensor.ws90_temperature.*"state": "33.3"' 40
  sleep 3
  ! grep -q "lock.front_door" <<< "$(station_events 5000 observation.state)" || die "a change of a non-allowlisted entity was recorded"
  ok

  step farm-ha-restart
  before="$(count_events station.connected)"
  ha_admin POST /_lab/restart '{"seconds":6}' >/dev/null
  wait_events station.disconnected 'station.disconnected' 40
  for _ in $(seq 1 40); do
    [ "$(count_events station.connected)" -gt "$before" ] && break
    sleep 2
  done
  [ "$(count_events station.connected)" -gt "$before" ] || die "the cell did not reconnect after Home Assistant came back"
  ha_admin POST /_lab/set '{"entity_id":"sensor.wh51_ch2_soil_moisture","state":"57"}' >/dev/null
  wait_events observation.state 'sensor.wh51_ch2_soil_moisture.*"state": "57"' 40
  ok

  step farm-heartbeat
  out="$(station_events 1 cell.heartbeat)"
  grep -q '"connected": true' <<< "$out" || die "the latest heartbeat does not say connected: $out"
  rss="$(sed -n 's/.*"rss_kb": \([0-9]*\).*/\1/p' <<< "$out")"
  [ -n "$rss" ] && [ "$rss" -lt 300000 ] || die "heartbeat memory out of range (${rss:-none} kB; the target is under 300 MB)"
  ok

  step farm-no-writes
  grep -q '"writes": \[\]' <<< "$(ha_admin GET /_lab/writes)" || die "something wrote to Home Assistant: $(ha_admin GET /_lab/writes)"
  ok
}

verify_farm_bot() { # /farm in Telegram shows what the cell saw, and only that
  local out
  step farm-bot; tg POST /_lab/clear '{}' >/dev/null; cmd_say "/farm"
  wait_reply 'connected to home assistant' 90
  out="$(tg GET /_lab/sent | tr '[:upper:]' '[:lower:]')"
  grep -q "ws90 temperature" <<< "$out" || die "/farm does not show the weather station: $out"
  grep -q "wh51 ch1 soil moisture" <<< "$out" || die "/farm does not show the soil sensor: $out"
  if grep -qE "front door|alarm|owner phone|do not leak" <<< "$out"; then die "/farm showed an entity that is not on the allowlist"; fi
  ok
}

cmd_verify() {
  if [ "${1:-}" = farm ]; then # only the farm scenarios (needs just `up --only farm`)
    farm_on || die "the farm profile is not up: ops/lab/lab.sh up --only farm"
    verify_farm; say "farm scenarios passed"; return 0
  fi
  mock_on || die "verify needs the mocked Telegram API: ops/lab/lab.sh up --mock-telegram"
  local log="$LAB_DIR/processor/mock-llm/mock-llm.jsonl" code
  { : > "$log"; } 2>/dev/null || true

  step status;   tg POST /_lab/clear '{}' >/dev/null; cmd_say "/status"; wait_reply 'edge server' 60; ok

  step ask;      tg POST /_lab/clear '{}' >/dev/null; cmd_say "/ask how is the node?"
  wait_reply 'mock-llm' 90
  grep -q "how is the node" "$log" || die "the question never reached mock-llm ($log)"; ok

  if grant_on; then
    # the owner's A1 grant: the assistant's restart proposal is permitted, waits for the owner, then runs
    step ask-grant; tg POST /_lab/clear '{}' >/dev/null; cmd_say "/ask frigate looks stuck, no detections"
    wait_reply 'waits for you as' 90
    req="$(tg GET /_lab/sent | grep -oE 'waits for you as [A-Za-z0-9_]+' | head -1 | awk '{print $NF}')"
    [ -n "$req" ] || die "no approval request id in the reply"
    tg POST /_lab/clear '{}' >/dev/null; cmd_say "/pending"; wait_reply 'approval\(s\) waiting' 60
    tg POST /_lab/clear '{}' >/dev/null; cmd_say "/approve $req $(fresh_code)"
    wait_reply 'approved; the action is' 90
    tg POST /_lab/clear '{}' >/dev/null; cmd_say "/audit"; wait_reply 'approval.requested' 60
    case "$(tg GET /_lab/sent)" in *action.started*) ;; *) die "no action.started in the audit after the approval" ;; esac
    ok
  else
    step ask-restart; tg POST /_lab/clear '{}' >/dev/null; cmd_say "/ask frigate looks stuck, no detections"
    wait_reply 'restarting frigate may help|waits for you as' 90
    tg POST /_lab/clear '{}' >/dev/null
    cmd_say "/restart frigate $(fresh_code)"
    wait_reply 'restarted frigate' 150; ok
  fi

  step model-error; "$PROC/lab/lab.sh" llm error >/dev/null; tg POST /_lab/clear '{}' >/dev/null
  cmd_say "/ask are you there?"; wait_reply 'no model answered|assistant failed' 150
  "$PROC/lab/lab.sh" llm normal >/dev/null; ok

  step audit;    tg POST /_lab/clear '{}' >/dev/null; cmd_say "/audit"; wait_reply 'audit log of' 60; ok

  step reboot;   tg POST /_lab/clear '{}' >/dev/null; code="$(fresh_code)"; cmd_say "/reboot $code"
  wait_reply 'back online after reboot' 180; ok

  if farm_on; then verify_farm; verify_farm_bot; fi

  step no-leaks
  local tok totp key
  tok="$(secret LAB_BOT_TOKEN)"; totp="$(secret LAB_TOTP_SECRET)"; key="$(cat "$LAB_DIR/processor/secrets/bot-api-key" 2>/dev/null || true)"
  [ -s "$log" ] || die "no model log to check"
  for v in "$totp" "$key"; do
    [ -z "$v" ] || { ! grep -qF "$v" "$log" || die "a secret reached the model log"; }
  done
  [ -z "$tok" ] || [ "$tok" = "123456789:labmocktokenaaaaaaaaaaaaaaaaaaaaaaa" ] || { ! grep -qF "$tok" "$log" || die "the bot token reached the model log"; }
  ok
  say "verify: all scenarios passed"
}

case "${1:-}" in
  up) shift; cmd_up "$@" ;;
  down) cmd_down ;;
  reset) cmd_reset ;;
  status) cmd_status ;;
  logs) shift; cmd_logs "$@" ;;
  check) preflight; cmd_check ;;
  verify) shift; cmd_verify "$@" ;;
  scenarios) cmd_scenarios ;;
  llm) shift; cmd_llm "$@" ;;
  code) cmd_code ;;
  say) shift; cmd_say "$@" ;;
  replies) shift; cmd_replies "$@" ;;
  cam) shift; cmd_cam "$@" ;;
  farm) shift; cmd_farm "$@" ;;
  *) sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' >&2; exit 2 ;;
esac
