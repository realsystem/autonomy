import base64
import copy
import json
import sys
import threading
from pathlib import Path

import pytest

import autonomy_core as ac
from autonomy_core import Freshness, KeyRing, Reject

REPO = Path(__file__).resolve().parents[3]
SPEC = REPO / "spec"
VECTORS = SPEC / "vectors"
FIXTURES = SPEC / "fixtures" / "v0"
TEST_KEYS = json.loads((SPEC / "fixtures" / "keys" / "test-keys.json").read_text())
KEYS = KeyRing(TEST_KEYS["public"])


def load(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def key_for(name):
    return ac.private_key_from_seed(bytes.fromhex(TEST_KEYS["private_seed_hex"][name]))


@pytest.fixture(scope="module")
def conformance():
    """The spec's own validator, to check what this library produces."""
    sys.path.insert(0, str(SPEC))
    try:
        from autonomy_conformance import checks
    except ImportError:  # pragma: no cover
        pytest.skip("spec conformance kit not importable")
    finally:
        sys.path.remove(str(SPEC))

    def validate(kind, obj):
        errors = [
            f
            for f in checks.validate(kind, obj, "$", checks.Context(keys=TEST_KEYS["public"]))
            if f.level == "error"
        ]
        assert not errors, "\n".join(map(str, errors))

    return validate


# --- spec vectors -------------------------------------------------------------


@pytest.mark.parametrize("path", sorted((VECTORS / "jcs").glob("*.json")), ids=lambda p: p.stem)
def test_jcs_vectors(path):
    v = load(path)
    assert ac.canonicalize(v["input"]).decode() == v["canonical"]


@pytest.mark.parametrize("bad", [float("nan"), float("-inf"), -(2**53), "\udc00", {2: 1}, b"x"])
def test_jcs_rejects(bad):
    with pytest.raises(ac.CanonicalizationError):
        ac.canonicalize(bad)


def test_params_and_audit_hash_vectors():
    v = load(VECTORS / "hash" / "params-hash.json")
    assert ac.params_hash(v["action"]) == v["params_hash"]
    v = load(VECTORS / "hash" / "audit-hash.json")
    assert ac.audit_hash(v["record"]) == v["hash"]


def test_signature_vector_both_directions():
    v = load(VECTORS / "signature" / "envelope.json")
    key = ac.private_key_from_seed(bytes.fromhex(v["private_seed_hex"]))
    assert ac.encode_public_key(key) == v["public_key"]
    assert ac.signing_input(v["envelope"], "envelope").decode() == v["signing_input"]
    resigned = ac.sign_object(copy.deepcopy(v["envelope"]), "envelope", key)
    assert resigned["autonomysig"] == v["autonomysig"]  # Ed25519 is deterministic
    assert (
        ac.verify_object(v["envelope"], "envelope", KeyRing({v["signer"]: v["public_key"]}))
        == v["envelope"]["source"]
    )


# --- spec fixtures ------------------------------------------------------------


@pytest.mark.parametrize(
    "path", sorted((FIXTURES / "envelope" / "valid").glob("*.json")), ids=lambda p: p.stem
)
def test_valid_envelope_fixtures_accepted(path):
    env = load(path)
    now = ac.parse_rfc3339(env["time"])
    assert ac.verify_envelope(env, KEYS, now=now) == env["source"]


@pytest.mark.parametrize(
    "name,reason",
    [
        ("missing-signature", "malformed"),
        ("wrong-specversion", "unknown protocol version"),
        ("tampered-data", "bad signature"),
        ("uppercase-source", "malformed"),
        ("data-base64", "malformed"),
        ("expires-before-time", "malformed"),
    ],
)
def test_invalid_envelope_fixtures_rejected(name, reason):
    env = load(FIXTURES / "envelope" / "invalid" / f"{name}.json")["instance"]
    with pytest.raises(Reject) as why:
        ac.verify_envelope(env, KEYS, now=ac.parse_rfc3339("2026-11-06T18:20:11Z"))
    assert why.value.reason == reason


SIGNED_FIXTURES = [
    (kind, p)
    for kind in ("grant", "decision", "approval", "execution-record", "verification")
    for p in sorted((FIXTURES / kind / "valid").glob("*.json"))
]


@pytest.mark.parametrize("kind,path", SIGNED_FIXTURES, ids=lambda x: getattr(x, "stem", x))
def test_signed_fixtures_verify(kind, path):
    obj = load(path)
    ac.verify_object(obj, kind, KEYS)
    obj["signature"] = obj["signature"][:-2] + ("AA" if not obj["signature"].endswith("AA") else "BB")
    with pytest.raises(Reject, match="bad signature"):
        ac.verify_object(obj, kind, KEYS)


def test_audit_chain_fixture():
    lines = (FIXTURES / "audit-record" / "valid" / "chain.jsonl").read_text().splitlines()
    chain = [json.loads(line) for line in lines]
    assert ac.verify_chain(chain, KEYS) == []
    assert any("does not link" in p for p in ac.verify_chain([chain[0], chain[2]], KEYS))
    assert any("genesis" in p for p in ac.verify_chain(chain[1:], KEYS))


# --- envelopes ----------------------------------------------------------------


def make(**kw):
    args = {
        "source": "cell:field-station",
        "type": "health.heartbeat",
        "data": {"uptime": 1},
        "seq": 7,
        "now": 1_800_000_000.0,
        "ttl": 180,
    }
    args.update(kw)
    return ac.make_envelope(key_for("cell:field-station"), **args)


def test_made_envelopes_conform(conformance):
    conformance("envelope", make())
    conformance(
        "envelope",
        make(ttl=None, subject="x", causationid=ac.new_ulid(), trust="untrusted", privacy="private"),
    )
    hb = make(
        type="health.reported",
        dataschema="https://autonomy.local/spec/v0/health.schema.json",
        data={"status": "ok", "component_id": "cell:field-station", "version": "0.1.0"},
    )
    conformance("envelope", hb)


def test_envelope_roundtrip_and_tamper():
    env = make()
    assert ac.verify_envelope(env, KEYS, now=1_800_000_010.0) == "cell:field-station"
    for field, value in [
        ("data", {"uptime": 2}),
        ("seq", 8),
        ("type", "health.reported"),
        ("trust", "owner"),
    ]:
        bad = copy.deepcopy(env)
        bad[field] = value
        with pytest.raises(Reject, match="bad signature"):
            ac.verify_envelope(bad, KEYS, now=1_800_000_010.0)


def test_instance_source_uses_component_key():
    env = make(source="cell:field-station@field-station")
    assert ac.verify_envelope(env, KEYS, now=1_800_000_000.0) == "cell:field-station@field-station"


def test_unknown_signer():
    with pytest.raises(Reject, match="unknown signer"):
        ac.verify_envelope(make(), KeyRing(), now=1_800_000_000.0)


@pytest.mark.parametrize(
    "now,freshness,reason",
    [
        (1_800_000_181.0, Freshness(), "expired"),
        (1_800_000_000.0 - 121, Freshness(), "from the future"),
        (1_800_000_000.0, Freshness(max_lifetime=60), "lifetime too long"),
        (1_800_000_000.0, Freshness(max_lifetime=180), None),
    ],
)
def test_freshness(now, freshness, reason):
    if reason is None:
        ac.verify_envelope(make(), KEYS, now=now, freshness=freshness)
        return
    with pytest.raises(Reject) as why:
        ac.verify_envelope(make(), KEYS, now=now, freshness=freshness)
    assert why.value.reason == reason


def test_envelope_without_expires():
    env = make(ttl=None)
    ac.verify_envelope(env, KEYS, now=1_800_000_500.0)
    with pytest.raises(Reject, match="expired"):
        ac.verify_envelope(env, KEYS, now=1_800_000_601.0)
    with pytest.raises(Reject, match="expires is required"):
        ac.verify_envelope(env, KEYS, now=1_800_000_000.0, freshness=Freshness(require_expires=True))


@pytest.mark.parametrize(
    "mutate",
    [
        lambda e: e.pop("correlationid"),
        lambda e: e.update(seq=-1),
        lambda e: e.update(seq=True),
        lambda e: e.update(source="nobody"),
        lambda e: e.update(trust="maybe"),
        lambda e: e.update(time="yesterday"),
    ],
)
def test_structure(mutate):
    env = make()
    mutate(env)
    with pytest.raises(Reject, match="malformed"):
        ac.verify_envelope(env, KEYS, now=1_800_000_000.0)


def test_replay_protection_survives_restart(tmp_path):
    env = make()
    seen = ac.SeenStore(tmp_path / "seen.json")
    ac.verify_envelope(env, KEYS, now=1_800_000_000.0, seen=seen)
    with pytest.raises(Reject, match="replayed"):
        ac.verify_envelope(env, KEYS, now=1_800_000_001.0, seen=seen)
    again = ac.SeenStore(tmp_path / "seen.json")
    with pytest.raises(Reject, match="replayed"):
        ac.verify_envelope(env, KEYS, now=1_800_000_002.0, seen=again)


def test_unsigned_flood_does_not_fill_seen_store():
    seen = ac.SeenStore(None, max_entries=1)
    bad = make()
    bad["data"] = {"x": 1}
    for _ in range(3):
        with pytest.raises(Reject, match="bad signature"):
            ac.verify_envelope(bad, KEYS, now=1_800_000_000.0, seen=seen)
    assert not seen.ids


def test_extensions_cannot_overwrite():
    with pytest.raises(ValueError):
        make(extensions={"source": "x"})
    assert make(extensions={"recipient": "cell:hub"})["recipient"] == "cell:hub"


# --- state ----------------------------------------------------------------------


def test_seen_store_expiry_and_capacity(tmp_path):
    seen = ac.SeenStore(tmp_path / "s.json", max_entries=2)
    assert seen.add("a", 10, 0) and not seen.add("a", 10, 1)
    assert seen.add("b", 10, 1)
    with pytest.raises(Reject, match="too many"):
        seen.add("c", 10, 2)
    assert seen.add("c", 20, 11)  # a and b expired
    (tmp_path / "bad.json").write_text("{not json")
    assert ac.SeenStore(tmp_path / "bad.json").ids == {}


def test_counter_persists_and_is_thread_safe(tmp_path):
    c = ac.Counter(tmp_path / "seq", start=100)
    assert c.next() == 101
    threads = [threading.Thread(target=lambda: [c.next() for _ in range(50)]) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert ac.Counter(tmp_path / "seq", start=0).next() == 302
    (tmp_path / "junk").write_text("x")
    assert ac.Counter(tmp_path / "junk", start=5).next() == 6


def test_outbox_handles_both_expiry_forms(tmp_path):
    box = ac.Outbox(tmp_path / "o.json")
    box.add({"id": "legacy", "expires": 100})
    box.add({"id": "env", "expires": ac.to_rfc3339(200)})
    box.add({"id": "none"})
    assert [m["id"] for m in box.pending(50)] == ["legacy", "env"]
    assert [m["id"] for m in box.pending(150)] == ["env"]
    assert [m["id"] for m in ac.Outbox(tmp_path / "o.json").pending(150)] == ["env"]
    box.done("env")
    assert len(ac.Outbox(tmp_path / "o.json")) == 0


def test_audit_log_appends_a_valid_chain(tmp_path, conformance):
    key = key_for("svc:action-gateway")
    log = ac.AuditLog(
        tmp_path / "audit.jsonl", cell="field-station", actor="svc:action-gateway@field-station", key=key
    )
    for i in range(3):
        log.append(
            "action.succeeded",
            summary=f"step {i}",
            correlationid="corr_x",
            refs={"action": f"act_{i}"},
            now=1_800_000_000.0 + i,
        )
    reopened = ac.AuditLog(
        tmp_path / "audit.jsonl", cell="field-station", actor="svc:action-gateway@field-station", key=key
    )
    rec = reopened.append(
        "action.verified",
        summary="done",
        correlationid="corr_x",
        config_hash="sha256:" + "a" * 64,
        now=1_800_000_010.0,
    )
    chain = list(reopened.records())
    assert rec["seq"] == 3 and len(chain) == 4
    assert ac.verify_chain(chain, KEYS) == []
    for r in chain:
        conformance("audit-record", r)
    chain[1]["summary"] = "edited"
    assert any("hash does not match" in p for p in ac.verify_chain(chain))


# --- keys and ids -----------------------------------------------------------


def test_key_encodings(tmp_path):
    key = ac.generate_private_key()
    text = ac.encode_public_key(key)
    assert text.startswith("ed25519:") and len(text) == 51
    raw = base64.urlsafe_b64decode(text[8:] + "=")
    legacy = base64.b64encode(raw).decode()  # edge_server fleet.yaml form
    assert ac.encode_public_key(ac.decode_public_key(legacy)) == text
    for bad in ["", "ed25519:abc", "AAAA", "ed25519:" + "A" * 43 + "=", 5]:
        with pytest.raises(ValueError):
            ac.decode_public_key(bad)
    ac.save_private_key(key, tmp_path / "k.pem")
    assert (tmp_path / "k.pem").stat().st_mode & 0o777 == 0o600
    assert ac.encode_public_key(ac.load_private_key(tmp_path / "k.pem")) == text
    sig = ac.sign(key, b"hello")
    assert ac.verify(ac.decode_public_key(text), b"hello", sig)
    assert not ac.verify(ac.decode_public_key(text), b"hello!", sig)
    assert not ac.verify(ac.decode_public_key(text), b"hello", "rsa:xyz")


def test_ids():
    u = ac.new_ulid(1_800_000_000.0)
    assert len(u) == 26 and u[0] in "01234567" and ac.new_ulid(1_800_000_000.001) > u[:10]
    assert ac.prefixed_id("dec").startswith("dec_")
    tp = ac.new_traceparent()
    assert len(tp) == 55 and ac.child_traceparent(tp)[:36] == tp[:36] and ac.child_traceparent(tp) != tp
    assert ac.is_name("field-station") and not ac.is_name("Field") and not ac.is_name("a--b")
    assert ac.is_component_ref("svc:pdp@field-station") and ac.is_component_ref("owner:ops")
    assert not ac.is_component_ref("owner:bob")


@pytest.mark.parametrize(
    "epoch,text", [(1_800_000_000.0, "2027-01-15T08:00:00Z"), (1_800_000_000.25, "2027-01-15T08:00:00.250Z")]
)
def test_rfc3339(epoch, text):
    assert ac.to_rfc3339(epoch) == text
    assert ac.parse_rfc3339(text) == epoch


@pytest.mark.parametrize(
    "bad", ["2027-01-15T08:00:00", "2027-01-15T08:00:00+00:00", "2027-01-15T08:00:00.Z", 5]
)
def test_rfc3339_rejects(bad):
    with pytest.raises(ValueError):
        ac.parse_rfc3339(bad)
