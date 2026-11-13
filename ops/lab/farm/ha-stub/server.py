#!/usr/bin/env python3
"""ha-stub: Home Assistant's WebSocket and REST read API, with a simulated Ecowitt GW3000
gateway, WS90 weather station and WH51 soil sensors, for the lab. Standard library only.

  WS   /api/websocket   auth, get_states, subscribe_events (state_changed), ping
  GET  /api/states, /api/states/<id>   (Bearer token)
  GET  /health
Anything that would WRITE (call_service and any other command) is refused and recorded:
  GET  /_lab/writes     -> {"writes": [...]}          the lab's check that nothing wrote
Admin:
  POST /_lab/set      {"entity_id": "...", "state": "..."}   change a state (emits state_changed)
  POST /_lab/restart  {"seconds": 5}                          drop every connection, refuse new ones
  GET  /_lab/state    -> all entities (no token needed)

The simulator moves the sensors every SIM_TICK seconds (diurnal temperature, sun, wet-up and
drying of soil, a little rain). Entity ids follow the Ecowitt integration's naming style.
"""

import base64
import hashlib
import json
import math
import os
import random
import socketserver
import struct
import threading
import time

TOKEN = os.environ.get("HA_TOKEN", "lab-ha-token")
TICK = float(os.environ.get("SIM_TICK", "5"))
PORT = int(os.environ.get("PORT", "8123"))
GUID = b"258EAF5A-9A8E-11C5-A9DC-65B49B1A9ADC"

LOCK = threading.RLock()
STATES: dict = {}
CLIENTS: list = []          # [(socket, send_lock, subscription ids)]
WRITES: list = []
DOWN_UNTIL = [0.0]


def iso(t=None):
    return time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(t or time.time()))


def put(entity_id, state, **attrs):
    """Set a state; emit state_changed if it differs."""
    with LOCK:
        old = STATES.get(entity_id)
        if old and old["state"] == str(state) and not attrs:
            return
        new = {"entity_id": entity_id, "state": str(state),
               "attributes": {**(old["attributes"] if old else {}), **attrs},
               "last_changed": iso(), "last_updated": iso()}
        STATES[entity_id] = new
        event = {"type": "event", "event": {"event_type": "state_changed", "time_fired": iso(),
                 "data": {"entity_id": entity_id, "old_state": old, "new_state": new}}}
        for sock, send_lock, subs in list(CLIENTS):
            for sid in list(subs):
                try:
                    ws_send(sock, send_lock, {**event, "id": sid})
                except OSError:
                    pass


def init_entities():
    meta = {
        "sensor.ws90_temperature": ("21.0", "°C", "temperature"),
        "sensor.ws90_humidity": ("55", "%", "humidity"),
        "sensor.ws90_wind_speed": ("2.0", "m/s", "wind_speed"),
        "sensor.ws90_wind_gust": ("3.5", "m/s", "wind_speed"),
        "sensor.ws90_wind_direction": ("180", "°", None),
        "sensor.ws90_rain_rate": ("0.0", "mm/h", "precipitation_intensity"),
        "sensor.ws90_daily_rain": ("0.0", "mm", "precipitation"),
        "sensor.ws90_solar_radiation": ("0", "W/m²", "irradiance"),
        "sensor.ws90_uv_index": ("0", "UV index", None),
        "sensor.ws90_battery": ("2.9", "V", "voltage"),
        "sensor.gw3000_indoor_temperature": ("22.5", "°C", "temperature"),
        "sensor.gw3000_indoor_humidity": ("48", "%", "humidity"),
        "sensor.gw3000_relative_pressure": ("1013.0", "hPa", "pressure"),
        "sensor.gw3000_absolute_pressure": ("1005.0", "hPa", "pressure"),
        "sensor.wh51_ch1_soil_moisture": ("38", "%", "moisture"),
        "sensor.wh51_ch2_soil_moisture": ("34", "%", "moisture"),
        "sensor.wh51_ch3_soil_moisture": ("41", "%", "moisture"),
        "sensor.wh51_ch1_battery": ("1.4", "V", "voltage"),
        "sensor.wh51_ch2_battery": ("1.4", "V", "voltage"),
        "sensor.wh51_ch3_battery": ("1.3", "V", "voltage"),
        "sensor.smartshunt_voltage": ("13.1", "V", "voltage"),
        "sensor.eco_worthy_battery": ("87", "%", "battery"),
        "input_number.farm_irrigation_recommended_climate_factor": ("1.0", None, None),
    }
    for eid, (state, unit, dev) in meta.items():
        attrs = {"friendly_name": eid.split(".", 1)[1].replace("_", " ")}
        if unit:
            attrs["unit_of_measurement"] = unit
        if dev:
            attrs["device_class"] = dev
        put(eid, state, **attrs)
    # not for the cell: it must never see these
    put("lock.front_door", "locked", friendly_name="front door")
    put("alarm_control_panel.home", "armed_away", friendly_name="alarm")
    put("sensor.owner_phone_location", "home", friendly_name="owner phone")
    put("sensor.secret_api_notes", "do not leak", friendly_name="notes")


def simulate():
    rain = 0.0
    soil = {1: 38.0, 2: 34.0, 3: 41.0}
    while True:
        time.sleep(TICK)
        if time.time() < DOWN_UNTIL[0]:
            continue
        hour = (time.time() % 86400) / 3600.0
        sun = max(0.0, math.sin((hour - 6) / 12 * math.pi))
        temp = 16 + 10 * sun + random.uniform(-0.3, 0.3)
        rain = max(0.0, rain + random.uniform(-0.4, 0.5)) if random.random() < 0.1 or rain else 0.0
        for n in soil:
            soil[n] = min(60.0, max(12.0, soil[n] + 0.3 * rain - 0.05 * (1 + sun) + random.uniform(-0.05, 0.05)))
            put(f"sensor.wh51_ch{n}_soil_moisture", f"{soil[n]:.0f}")
        put("sensor.ws90_temperature", f"{temp:.1f}")
        put("sensor.ws90_humidity", f"{max(20, 80 - 35 * sun + random.uniform(-2, 2)):.0f}")
        put("sensor.ws90_wind_speed", f"{max(0, 2 + random.uniform(-1, 2)):.1f}")
        put("sensor.ws90_rain_rate", f"{rain:.1f}")
        put("sensor.ws90_solar_radiation", f"{900 * sun:.0f}")
        put("sensor.ws90_uv_index", f"{round(8 * sun)}")


# -- WebSocket (server side) ------------------------------------------------------------------

def ws_send(sock, send_lock, obj):
    data = json.dumps(obj).encode()
    n = len(data)
    head = bytes([0x81]) + (bytes([n]) if n < 126 else bytes([126]) + struct.pack(">H", n)
                            if n < 65536 else bytes([127]) + struct.pack(">Q", n))
    with send_lock:
        sock.sendall(head + data)


def ws_recv(rfile):
    """One text message from the client, or None when it closes."""
    while True:
        h = rfile.read(2)
        if len(h) < 2:
            return None
        opcode, n = h[0] & 0x0F, h[1] & 0x7F
        if n == 126:
            n = struct.unpack(">H", rfile.read(2))[0]
        elif n == 127:
            n = struct.unpack(">Q", rfile.read(8))[0]
        mask = rfile.read(4) if h[1] & 0x80 else b"\0\0\0\0"
        payload = bytes(c ^ mask[i % 4] for i, c in enumerate(rfile.read(n)))
        if opcode == 0x8:
            return None
        if opcode in (0x1, 0x0):
            return payload.decode()


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        try:
            self._handle()
        except (OSError, ValueError):
            pass

    def _http(self, code, body, ctype="application/json"):
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.wfile.write(f"HTTP/1.1 {code} X\r\nContent-Type: {ctype}\r\nContent-Length: {len(raw)}\r\n"
                         f"Connection: close\r\n\r\n".encode() + raw)

    def _handle(self):
        line = self.rfile.readline().decode("latin-1").strip()
        if not line:
            return
        method, path, _ = line.split(" ", 2)
        headers = {}
        while True:
            h = self.rfile.readline().decode("latin-1").strip()
            if not h:
                break
            k, _, v = h.partition(":")
            headers[k.strip().lower()] = v.strip()
        if time.time() < DOWN_UNTIL[0] and not path.startswith("/_lab"):
            return self._http(503, {"error": "Home Assistant is starting"})
        length = int(headers.get("content-length", 0))
        body = self.rfile.read(length) if length else b""
        if headers.get("upgrade", "").lower() == "websocket" and path == "/api/websocket":
            return self._websocket(headers)
        authed = headers.get("authorization") == f"Bearer {TOKEN}"
        if path == "/health":
            return self._http(200, {"status": "ok"})
        if path == "/_lab/writes":
            with LOCK:
                return self._http(200, {"writes": list(WRITES)})
        if path == "/_lab/state":
            with LOCK:
                return self._http(200, STATES)
        if path == "/_lab/set" and method == "POST":
            b = json.loads(body or b"{}")
            put(b["entity_id"], b["state"])
            return self._http(200, {"ok": True})
        if path == "/_lab/restart" and method == "POST":
            seconds = float(json.loads(body or b"{}").get("seconds", 5))
            DOWN_UNTIL[0] = time.time() + seconds
            with LOCK:
                for sock, _, _ in list(CLIENTS):
                    try:
                        sock.shutdown(2)
                    except OSError:
                        pass
            return self._http(200, {"ok": True, "down_for": seconds})
        if not authed:
            return self._http(401, {"message": "unauthorized"})
        if path == "/api/states":
            with LOCK:
                return self._http(200, list(STATES.values()))
        if path.startswith("/api/states/"):
            with LOCK:
                s = STATES.get(path.rsplit("/", 1)[-1])
            return self._http(200 if s else 404, s or {"message": "not found"})
        if method != "GET":
            with LOCK:
                WRITES.append({"via": "rest", "method": method, "path": path, "at": iso()})
            return self._http(403, {"message": "the lab stub does not take writes"})
        self._http(404, {"message": "not found"})

    def _websocket(self, headers):
        accept = base64.b64encode(hashlib.sha1(headers["sec-websocket-key"].encode() + GUID).digest()).decode()
        self.wfile.write(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                          f"Sec-WebSocket-Accept: {accept}\r\n\r\n").encode())
        sock, send_lock, subs = self.request, threading.Lock(), []
        ws_send(sock, send_lock, {"type": "auth_required", "ha_version": "lab-stub"})
        msg = json.loads(ws_recv(self.rfile) or "{}")
        if msg.get("type") != "auth" or msg.get("access_token") != TOKEN:
            ws_send(sock, send_lock, {"type": "auth_invalid", "message": "Invalid access token"})
            return
        ws_send(sock, send_lock, {"type": "auth_ok", "ha_version": "lab-stub"})
        entry = (sock, send_lock, subs)
        with LOCK:
            CLIENTS.append(entry)
        try:
            while True:
                text = ws_recv(self.rfile)
                if text is None:
                    return
                m = json.loads(text)
                kind, mid = m.get("type"), m.get("id")
                if kind == "get_states":
                    with LOCK:
                        ws_send(sock, send_lock, {"id": mid, "type": "result", "success": True,
                                                  "result": list(STATES.values())})
                elif kind == "subscribe_events" and m.get("event_type") == "state_changed":
                    subs.append(mid)
                    ws_send(sock, send_lock, {"id": mid, "type": "result", "success": True, "result": None})
                elif kind == "ping":
                    ws_send(sock, send_lock, {"id": mid, "type": "pong"})
                else:  # call_service and everything else that would change or expose more
                    with LOCK:
                        WRITES.append({"via": "ws", "message": m, "at": iso()})
                    ws_send(sock, send_lock, {"id": mid, "type": "result", "success": False,
                                              "error": {"code": "unauthorized",
                                                        "message": "the lab stub does not take this command"}})
        finally:
            with LOCK:
                if entry in CLIENTS:
                    CLIENTS.remove(entry)


class Server(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


def serve(port=PORT, simulate_sensors=True):
    init_entities()
    if simulate_sensors:
        threading.Thread(target=simulate, daemon=True).start()
    srv = Server(("0.0.0.0", port), Handler)
    return srv


if __name__ == "__main__":
    serve().serve_forever()
