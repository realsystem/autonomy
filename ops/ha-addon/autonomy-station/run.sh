#!/bin/sh
# Turns the add-on options (/data/options.json) into the station config and runs the cell.
set -eu
python - <<'PY'
import json
opts = json.load(open("/data/options.json"))
cfg = {
    "ha_url": "ws://supervisor/core/websocket",
    "allow": opts["allow"],
    "db": "/data/station-events.db",
    "heartbeat_s": opts.get("heartbeat_s", 30),
    "retention_days": opts.get("retention_days", 30),
}
json.dump(cfg, open("/data/station.json", "w"))
PY
exec autonomy station run --config /data/station.json
