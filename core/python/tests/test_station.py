"""M10: the read-only Field Station cell against the lab's Home Assistant stand-in (IT-60)."""

from __future__ import annotations

import importlib.util
import json
import threading
import time
import urllib.request
from pathlib import Path

import pytest

from autonomy_core.eventlog import EventLog
from autonomy_core.station import Station, StationConfig
from autonomy_core.world_ha import Allowlist, HaError, HaReader, NotAllowed
from autonomy_core.wsclient import WebSocket, WsError

STUB = Path(__file__).resolve().parents[3] / "ops/lab/farm/ha-stub/server.py"
pytestmark = pytest.mark.skipif(not STUB.exists(), reason="the lab's ha-stub is not in this checkout")

ALLOW = ["sensor.ws90_*", "sensor.wh51_*", "sensor.gw3000_*", "input_number.farm_irrigation_*"]


@pytest.fixture
def ha():
    spec = importlib.util.spec_from_file_location(f"ha_stub_{time.monotonic_ns()}", STUB)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    srv = mod.serve(port=0, simulate_sensors=False)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    mod.url = f"ws://127.0.0.1:{srv.server_address[1]}/api/websocket"
    mod.http = f"http://127.0.0.1:{srv.server_address[1]}"
    yield mod
    srv.shutdown()
    srv.server_close()


def post(ha, path, body):
    r = urllib.request.Request(
        ha.http + path, json.dumps(body).encode(), {"Content-Type": "application/json"}
    )
    return json.load(urllib.request.urlopen(r, timeout=5))


def reader(ha, allow=None):
    return HaReader(ha.url, ha.TOKEN, Allowlist(ALLOW if allow is None else allow), timeout=5)


class TestAllowlist:
    def test_patterns_and_empty(self):
        a = Allowlist(["sensor.ws90_*", "input_number.x"])
        assert a.allows("sensor.ws90_temperature") and a.allows("input_number.x")
        assert not a.allows("lock.front_door") and not a.allows("sensor.ws91_x")
        assert not Allowlist([]).allows("sensor.ws90_temperature")


class TestReader:
    def test_wrong_token_is_refused(self, ha):
        with pytest.raises(HaError, match="refused the token"):
            HaReader(ha.url, "wrong", Allowlist(ALLOW), timeout=5)

    def test_snapshot_returns_only_allowlisted(self, ha):
        r = reader(ha)
        try:
            ids = {o.entity_id for o in r.snapshot()}
        finally:
            r.close()
        assert "sensor.ws90_temperature" in ids and "sensor.wh51_ch1_soil_moisture" in ids
        assert "lock.front_door" not in ids and "sensor.secret_api_notes" not in ids
        assert "sensor.smartshunt_voltage" not in ids  # not on this list

    def test_an_entity_outside_the_allowlist_cannot_be_read_even_if_asked(self, ha):
        r = reader(ha)
        try:
            r.snapshot()
            assert r.read("sensor.ws90_temperature") is not None
            for entity in ("lock.front_door", "sensor.secret_api_notes", "sensor.owner_phone_location"):
                with pytest.raises(NotAllowed):
                    r.read(entity)
        finally:
            r.close()

    def test_events_are_filtered_too(self, ha):
        r = reader(ha)
        try:
            r.subscribe()
            r.snapshot()
            post(ha, "/_lab/set", {"entity_id": "lock.front_door", "state": "unlocked"})
            post(ha, "/_lab/set", {"entity_id": "sensor.ws90_temperature", "state": "30.5"})
            seen = []
            for _ in range(5):
                seen += r.events(0.3)
            assert [(o.entity_id, o.state) for o in seen] == [("sensor.ws90_temperature", "30.5")]
        finally:
            r.close()

    def test_attributes_are_reduced_to_a_known_few(self, ha):
        post(ha, "/_lab/set", {"entity_id": "sensor.ws90_humidity", "state": "50"})
        ha.STATES["sensor.ws90_humidity"]["attributes"]["entity_picture"] = "/secret/path"
        r = reader(ha)
        try:
            o = next(o for o in r.snapshot() if o.entity_id == "sensor.ws90_humidity")
        finally:
            r.close()
        assert "entity_picture" not in o.attributes and o.attributes["unit_of_measurement"] == "%"

    def test_nothing_that_writes_can_be_sent(self, ha):
        r = reader(ha)
        try:
            for msg in (
                {"type": "call_service", "domain": "light", "service": "turn_on"},
                {"type": "config/auth/list"},
                {"type": "subscribe_events", "event_type": "call_service"},
                {"type": "subscribe_events"},
            ):
                with pytest.raises(HaError, match="read-only"):
                    r._send(msg)
            assert not hasattr(r, "call_service")
        finally:
            r.close()
        assert ha.WRITES == []  # nothing reached Home Assistant

    def test_a_full_session_makes_no_write_at_home_assistant(self, ha):
        r = reader(ha)
        try:
            r.subscribe()
            r.snapshot()
            r.ping()
        finally:
            r.close()
        assert ha.WRITES == []


class TestWebSocket:
    def test_bad_url(self):
        with pytest.raises(WsError):
            WebSocket("http://example.org/")


class TestEventLog:
    def test_append_tail_latest_prune(self, tmp_path):
        log = EventLog(tmp_path / "e.db")
        log.append("observation.state", "sensor.a", {"state": "1"}, ts=time.time() - 100)
        log.append("observation.state", "sensor.a", {"state": "2"})
        log.append("cell.heartbeat", "c", {"n": 1})
        assert [e["data"]["state"] for e in log.tail(10, kind="observation.state")] == ["1", "2"]
        assert log.latest("observation.state", "sensor.a")["data"]["state"] == "2"
        assert log.count() == 3 and log.prune(50) == 1 and log.count() == 2
        log.close()
        assert EventLog(tmp_path / "e.db").count() == 2  # survives a reopen


class TestStation:
    def cfg(self, ha, tmp_path, **kw):
        return StationConfig(
            ha_url=ha.url,
            token=ha.TOKEN,
            allow=ALLOW,
            db=str(tmp_path / "e.db"),
            heartbeat_s=0.3,
            backoff_max_s=1.0,
            **kw,
        )

    def start(self, ha, tmp_path):
        log = EventLog(tmp_path / "e.db")
        st = Station(self.cfg(ha, tmp_path), log)
        t = threading.Thread(target=st.run, daemon=True)
        t.start()
        return st, log, t

    def wait(self, cond, timeout=10.0):
        end = time.time() + timeout
        while time.time() < end:
            if cond():
                return True
            time.sleep(0.05)
        return False

    def test_records_changes_not_repeats_and_only_allowlisted(self, ha, tmp_path):
        st, log, t = self.start(ha, tmp_path)
        try:
            assert self.wait(lambda: st.connected)
            n0 = log.count("observation.state")
            assert n0 >= 15
            post(ha, "/_lab/set", {"entity_id": "sensor.ws90_temperature", "state": "31.0"})
            post(ha, "/_lab/set", {"entity_id": "sensor.ws90_temperature", "state": "31.0"})  # same: no event
            post(ha, "/_lab/set", {"entity_id": "lock.front_door", "state": "unlocked"})  # not allowed
            assert self.wait(lambda: log.count("observation.state") == n0 + 1)
            subjects = {e["subject"] for e in log.tail(1000, kind="observation.state")}
            assert "lock.front_door" not in subjects
            assert self.wait(lambda: log.count("cell.heartbeat") >= 2)
            hb = log.latest("cell.heartbeat", "field-station")["data"]
            assert hb["connected"] is True and hb["entities"] >= 15
        finally:
            st.stopping = True
            t.join(timeout=5)
        assert ha.WRITES == []

    def test_survives_a_home_assistant_restart(self, ha, tmp_path):
        """IT-60: HA goes away and comes back; the cell reconnects and keeps recording."""
        st, log, t = self.start(ha, tmp_path)
        try:
            assert self.wait(lambda: st.connected)
            post(ha, "/_lab/restart", {"seconds": 1.5})
            assert self.wait(lambda: log.count("station.disconnected") >= 1)
            assert self.wait(lambda: log.count("station.connected") >= 2, timeout=20)
            post(ha, "/_lab/set", {"entity_id": "sensor.wh51_ch1_soil_moisture", "state": "55"})
            assert self.wait(
                lambda: any(
                    e["data"]["state"] == "55" for e in log.tail(50, subject="sensor.wh51_ch1_soil_moisture")
                )
            )
            beats = log.tail(100, kind="cell.heartbeat")
            assert any(b["data"]["connected"] is False for b in beats)  # it said so while HA was away
        finally:
            st.stopping = True
            t.join(timeout=5)

    def test_config_loading(self, tmp_path, monkeypatch):
        (tmp_path / "tok").write_text("from-file\n")
        (tmp_path / "c.json").write_text(
            json.dumps(
                {"ha_url": "ws://x/y", "allow": ["sensor.a"], "token_file": str(tmp_path / "tok"), "bogus": 1}
            )
        )
        monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
        monkeypatch.delenv("HA_TOKEN", raising=False)
        cfg = StationConfig.load(tmp_path / "c.json")
        assert cfg.token == "from-file" and cfg.allow == ["sensor.a"]
        monkeypatch.setenv("SUPERVISOR_TOKEN", "from-env")
        assert StationConfig.load(tmp_path / "c.json").token == "from-env"
