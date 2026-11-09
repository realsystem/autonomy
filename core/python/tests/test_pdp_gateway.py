"""C04 PDP and C06 Action Gateway (M5): IT-13 (one execution path), IT-15 (rate limits and
circuit breaker), crash reconciliation, approval binding at the gateway.

Tests that evaluate real Cedar need ``cedarpy``; CI installs it (AUTONOMY_REQUIRE_CEDAR=1).
"""

import copy
import json
import os
import sys
from pathlib import Path

import pytest

import autonomy_core as ac
from autonomy_core import Reject
from autonomy_core.gateway import ActionGateway, AdapterResult, MockAdapter
from autonomy_core.pdp import Pdp, Policy, PolicyBundle, decision_request, load_bundle

REPO = Path(__file__).resolve().parents[3]
SPEC = REPO / "spec"
FIX = SPEC / "fixtures" / "v0"
TK = json.loads((SPEC / "fixtures" / "keys" / "test-keys.json").read_text())
NOW = ac.parse_rfc3339("2026-11-15T12:00:00Z")
HELPER = "input_number.farm_irrigation_recommended_climate_factor"


def load(p):
    return json.loads(Path(p).read_text())


def key(name):
    return ac.private_key_from_seed(bytes.fromhex(TK["private_seed_hex"][name]))


class Clock:
    def __init__(self, t=NOW):
        self.now = t

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture(scope="module")
def registry():
    doc = load(FIX / "registry" / "valid" / "two-site.json")
    sig = load(FIX / "registry-signature" / "valid" / "ops.json")
    return ac.verify_registry(doc, sig, root=TK["public"]["owner:root"], now=NOW)


@pytest.fixture(scope="module")
def bundle(registry):
    return load_bundle(load(FIX / "policy-bundle" / "valid" / "farm.json"), registry, now=NOW)


def need_cedar():
    try:
        import cedarpy  # noqa: F401
    except ImportError:
        if os.environ.get("AUTONOMY_REQUIRE_CEDAR"):
            pytest.fail("cedarpy is required (AUTONOMY_REQUIRE_CEDAR is set)")
        pytest.skip("cedarpy not installed")


def proposal(value=118, proposer="agent:field-station", target=HELPER, key_suffix="2026-11-16"):
    p = load(FIX / "action-proposal" / "valid" / "climate-factor.json")
    p["proposer"] = proposer
    p["action"]["target"] = target
    p["action"]["params"] = {"value": value}
    p["idempotency_key"] = f"field-station:climate_factor:{key_suffix}"
    return p


def request(p, **ctx):
    args = {
        "cell": "field-station",
        "risk_class": "R1",
        "trust": "trusted",
        "evaluation": "ok",
        "now": NOW,
        "power_stage": "normal",
        "count_today": 0,
    }
    args.update(ctx)
    return decision_request(p, **args)


class FakeCedar:
    """Stands in for Cedar where only the PDP's own rules are under test."""

    def __init__(self, fn):
        self.fn = fn
        self.calls = []

    def __call__(self, request, policies, entities):
        self.calls.append(request)
        return self.fn(request)


def pdp(bundle, clock, cedar=None):
    return Pdp(issuer="svc:pdp@field-station", key=key("svc:pdp"), bundle=bundle, cedar=cedar, clock=clock)


# --- PDP: bundle loading --------------------------------------------------------------------


def test_bundle_fixture_loads(bundle):
    assert bundle.version == "2026.11.09-1" and [p.id for p in bundle.policies] == [
        "farm-helpers",
        "untrusted-needs-owner",
        "no-r3",
    ]
    assert bundle.policies[1].approvable and bundle.grants[0]["grant_id"] == "grt_2026_11_farm_helpers"


def test_bundle_must_be_owner_signed_and_not_older(registry, bundle):
    doc = load(FIX / "policy-bundle" / "valid" / "farm.json")
    tampered = copy.deepcopy(doc)
    tampered["policies"][2]["cedar"] = "forbid (principal, action, resource) when { false };"
    with pytest.raises(Reject, match="bad signature"):
        load_bundle(tampered, registry, now=NOW)
    newer = PolicyBundle("2026.11.10-1")
    with pytest.raises(Reject, match="rollback"):
        load_bundle(doc, registry, active=newer, now=NOW)
    grant_tampered = copy.deepcopy(doc)
    grant_tampered["grants"][0]["constraints"]["max_per_day"] = 400
    with pytest.raises(Reject, match="bad signature"):
        load_bundle(grant_tampered, registry, now=NOW)


# --- PDP: rules (local, fake Cedar) ---------------------------------------------------------------


def no_cedar_permit(request):
    return (False, [], [])


def test_grant_and_level(bundle, clock):
    p = pdp(bundle, clock, FakeCedar(no_cedar_permit))
    d = p.decide(request(proposal()))
    assert (
        d["effect"] == "require_approval" and "grt_2026_11_farm_helpers" in d["matched"]
    )  # A2 approves each
    assert {"type": "approval", "approver": "owner:ops"} in d["obligations"]
    assert {"type": "verify"} in d["obligations"]
    assert d["params_hash"] == ac.params_hash(proposal()["action"])
    ac.verify_object(d, "decision", ac.KeyRing({"svc:pdp": TK["public"]["svc:pdp"]}))
    a3 = PolicyBundle(bundle.version, (), tuple(dict(g, autonomy_level="A3") for g in bundle.grants))
    assert pdp(a3, clock).decide(request(proposal()))["effect"] == "permit"


@pytest.mark.parametrize(
    "change,effect,reason",
    [
        ({"risk_class": "R3"}, "deny", "never permitted"),
        ({"evaluation": "reject"}, "deny", "rejected"),
        ({"count_today": 4}, "deny", "already 4 today"),
        ({"cell": "home-hub"}, "deny", "cell home-hub not covered"),
        ({"power_stage": "low"}, "deny", "power stage low below normal"),
        ({"risk_class": "R2"}, "deny", "risk R2 above"),
    ],
)
def test_constraints_and_hard_rules(bundle, clock, change, effect, reason):
    a3 = PolicyBundle(bundle.version, (), tuple(dict(g, autonomy_level="A3") for g in bundle.grants))
    d = pdp(a3, clock).decide(request(proposal(), **change))
    assert d["effect"] == effect and reason in d["reason"]


def test_value_range_and_window(bundle, clock):
    a3 = PolicyBundle(bundle.version, (), tuple(dict(g, autonomy_level="A3") for g in bundle.grants))
    assert pdp(a3, clock).decide(request(proposal(value=250)))["effect"] == "deny"
    early = Clock(ac.parse_rfc3339("2026-11-01T00:00:00Z"))
    assert "not valid now" in pdp(a3, early).decide(request(proposal()))["reason"]


def test_escalations(bundle, clock):
    a3 = PolicyBundle(bundle.version, (), tuple(dict(g, autonomy_level="A3") for g in bundle.grants))
    assert pdp(a3, clock).decide(request(proposal(), trust="untrusted"))["effect"] == "require_approval"
    assert pdp(a3, clock).decide(request(proposal(), evaluation="escalate"))["effect"] == "require_approval"


def test_owner_and_r0_and_nothing(clock):
    empty = PolicyBundle.baseline()
    owner = proposal(proposer="owner:ops")
    assert pdp(empty, clock).decide(request(owner, owner_verified=True))["effect"] == "permit"
    assert pdp(empty, clock).decide(request(owner))["effect"] == "deny"  # not verified
    assert pdp(empty, clock).decide(request(proposal(), risk_class="R0"))["effect"] == "permit"
    d = pdp(empty, clock).decide(request(proposal()))
    assert d["effect"] == "deny" and "no grant or policy" in d["reason"]


def test_cedar_wrapper(clock):
    forbid = PolicyBundle("2026.11.09-2", (Policy("guard", "forbid (principal, action, resource);"),))
    p = pdp(forbid, clock, FakeCedar(lambda r: (False, ["policy0"], [])))
    d = p.decide(request(proposal(proposer="owner:ops"), owner_verified=True))
    assert d["effect"] == "deny" and d["matched"] == ["policy:guard"]  # forbid beats the owner
    approvable = PolicyBundle(
        "2026.11.09-3", (Policy("ask", "permit (principal, action, resource);", approvable=True),)
    )
    fake = FakeCedar(lambda r: (True, ["policy0"], []) if "approval" in r["context"] else (False, [], []))
    d = pdp(approvable, clock, fake).decide(request(proposal()))
    assert d["effect"] == "require_approval" and d["matched"] == ["policy:ask"]
    assert fake.calls[0]["principal"] == 'Agent::"field-station"'
    assert fake.calls[0]["resource"] == f'Resource::"{HELPER}"' and fake.calls[0]["context"]["params"] == {
        "value": 118
    }
    errors = pdp(approvable, clock, FakeCedar(lambda r: (False, [], ["type error"]))).decide(
        request(proposal())
    )
    assert errors["effect"] == "deny" and "policy evaluation error" in errors["reason"]


def test_fails_closed_without_cedar(bundle, clock, monkeypatch):
    monkeypatch.setitem(sys.modules, "cedarpy", None)
    d = Pdp(issuer="svc:pdp", key=key("svc:pdp"), bundle=bundle, clock=clock).decide(request(proposal()))
    assert d["effect"] == "deny" and "policy engine unavailable" in d["reason"]


def test_malformed_requests(bundle, clock):
    p = pdp(bundle, clock, FakeCedar(no_cedar_permit))
    for bad in [
        {},
        {**request(proposal()), "context": {"risk_class": "R9"}},
        {k: v for k, v in request(proposal()).items() if k != "proposal_id"},
    ]:
        with pytest.raises(Reject, match="malformed"):
            p.decide(bad)


# --- PDP with real Cedar (CI) ----------------------------------------------------------------------


def test_cedar_fixture_bundle(bundle, clock):
    need_cedar()
    p = Pdp(issuer="svc:pdp", key=key("svc:pdp"), bundle=bundle, clock=clock)
    d = p.decide(request(proposal()))
    assert d["effect"] == "require_approval", d["reason"]  # A2 grant; Cedar permits via farm-helpers
    assert "policy:farm-helpers" in d["matched"]
    d = p.decide(request(proposal(), risk_class="R3"))
    assert d["effect"] == "deny"
    # untrusted: farm-helpers does not apply; the approvable policy asks the owner
    stranger = proposal(proposer="agent:research", target="input_text.farm_agent_daily_note")
    d = p.decide(request(stranger, trust="untrusted"))
    assert d["effect"] == "require_approval" and d["matched"] == ["policy:untrusted-needs-owner"], d
    d = p.decide(request(stranger))
    assert d["effect"] == "deny"
    # group membership decides farm-helpers
    other = proposal(target="input_number.not_a_farm_helper")
    assert "policy:farm-helpers" not in p.decide(request(other))["matched"]


def test_cedar_forbid_over_owner(clock):
    need_cedar()
    b = PolicyBundle(
        "2026.11.09-5",
        (Policy("no-reboots", 'forbid (principal, action == Action::"host.reboot", resource);'),),
    )
    p = Pdp(issuer="svc:pdp", key=key("svc:pdp"), bundle=b, clock=clock)
    owner = proposal(proposer="owner:ops")
    owner["action"] = {"type": "host.reboot", "target": "host", "params": {}}
    assert p.decide(request(owner, owner_verified=True))["effect"] == "deny"
    owner["action"]["type"] = "host.restart_service"
    assert p.decide(request(owner, owner_verified=True))["effect"] == "permit"


# --- Gateway ---------------------------------------------------------------------------------


PDP_KEYS = ac.KeyRing({"svc:pdp": TK["public"]["svc:pdp"]})


def permit_bundle():
    g = load(FIX / "grant" / "valid" / "farm-helpers.json")
    return PolicyBundle("2026.11.09-1", (), (dict(g, autonomy_level="A3"),))


@pytest.fixture
def world(tmp_path, clock, registry):
    adapter = MockAdapter("adapter:ha@field-station", {"ha.helper.set"})
    audit = ac.AuditLog(
        tmp_path / "audit.jsonl",
        cell="field-station",
        actor="svc:action-gateway@field-station",
        key=key("svc:action-gateway"),
    )
    state = {"suspended": False}

    def make(adapters=None):
        return ActionGateway(
            executor="svc:action-gateway@field-station",
            key=key("svc:action-gateway"),
            state_dir=tmp_path / "gw",
            pdp_keys=PDP_KEYS,
            registry=registry,
            adapters=adapters or [adapter],
            audit=audit,
            clock=clock,
            suspended=lambda: state["suspended"],
        )

    class World:
        pass

    w = World()
    w.adapter, w.audit, w.make, w.state, w.gw = adapter, audit, make, state, make()
    w.pdp = pdp(permit_bundle(), clock)
    return w


def action_request(w, p, approval=None, **ctx):
    d = w.pdp.decide(request(p, **ctx))
    r = {
        "action_id": ac.prefixed_id("act"),
        "proposal": p,
        "decision": d,
        "idempotency_key": p["idempotency_key"],
    }
    if approval:
        r["approval"] = approval(d)
    return r


def approve(decision, signer="owner:ops", **change):
    a = {
        "approval_id": ac.prefixed_id("apr"),
        "decision_id": decision["decision_id"],
        "params_hash": decision["params_hash"],
        "approver": "owner:ops",
        "method": "telegram_totp",
        "approved_at": ac.to_rfc3339(NOW),
        "expires": ac.to_rfc3339(NOW + 900),
    }
    a.update(change)
    return ac.sign_object(a, "approval", key(signer))


def test_happy_path_is_recorded_and_audited(world):
    rec = world.gw.execute(action_request(world, proposal()))
    assert rec["status"] == "succeeded" and rec["verification_pending"] and rec["after_reported"] == 118
    ac.verify_object(
        rec, "execution-record", ac.KeyRing({"svc:action-gateway": TK["public"]["svc:action-gateway"]})
    )
    chain = list(world.audit.records())
    assert [r["kind"] for r in chain] == ["action.started", "action.succeeded"]
    assert ac.verify_chain(chain, ac.KeyRing(TK["public"])) == []
    assert (
        chain[1]["refs"]["decision"].startswith("dec_")
        and chain[1]["refs"]["proposal"] == proposal()["proposal_id"]
    )


def test_conforms_to_spec(world):
    sys.path.insert(0, str(SPEC))
    from autonomy_conformance import checks

    sys.path.remove(str(SPEC))
    rec = world.gw.execute(action_request(world, proposal()))
    rej = world.gw.execute(action_request(world, proposal(key_suffix="x"), risk_class="R3"))
    for kind, obj in [
        ("execution-record", rec),
        ("execution-record", rej),
        ("decision", world.pdp.decide(request(proposal()))),
    ]:
        errors = [
            f
            for f in checks.validate(kind, obj, "$", checks.Context(keys=TK["public"]))
            if f.level == "error"
        ]
        assert not errors, errors
    for r in world.audit.records():
        assert not [
            f
            for f in checks.validate("audit-record", r, "$", checks.Context(keys=TK["public"]))
            if f.level == "error"
        ]


def test_idempotency(world):
    r = action_request(world, proposal())
    first = world.gw.execute(r)
    again = world.gw.execute(dict(r, action_id=ac.prefixed_id("act")))
    assert again == first and len(world.adapter.calls) == 1
    assert world.make().execute(r) == first  # survives a restart


@pytest.mark.parametrize(
    "mutate,code",
    [
        (lambda r: r["proposal"]["action"]["params"].update(value=150), "validation.binding"),
        (lambda r: r["decision"].update(proposal_id="prp_other"), "validation.binding"),
        (lambda r: r["decision"].update(obligations=[]), "validation.decision_signature"),
        (lambda r: r.update(idempotency_key="other"), "validation.binding"),
        (lambda r: r["proposal"]["action"].update(type="ha.service.call"), "validation.binding"),
        (lambda r: r.pop("decision"), "validation.malformed"),
    ],
)
def test_tampering_is_rejected(world, mutate, code):
    r = action_request(world, proposal())
    mutate(r)
    rec = world.gw.execute(r)
    assert rec["status"] == "rejected" and rec["error"]["code"] == code, rec
    assert world.adapter.calls == []
    assert list(world.audit.records())[-1]["kind"] == "action.rejected"


def test_decision_effects_and_expiry(world, clock):
    assert (
        world.gw.execute(action_request(world, proposal(), risk_class="R3"))["error"]["code"]
        == "policy.denied"
    )
    r = action_request(world, proposal(key_suffix="late"))
    clock.now += 901
    assert world.gw.execute(r)["error"]["code"] == "validation.expired"
    foreign = action_request(world, proposal(key_suffix="foreign"))
    foreign["decision"] = ac.sign_object(
        dict(foreign["decision"], issuer="svc:verifier"), "decision", key("svc:verifier")
    )
    assert world.gw.execute(foreign)["error"]["code"] == "validation.decision_signature"


def test_approval_binding(world, clock):
    a2 = Pdp(
        issuer="svc:pdp",
        key=key("svc:pdp"),
        bundle=PolicyBundle(
            "2026.11.09-1", (), tuple(load(FIX / "grant" / "valid" / "farm-helpers.json") for _ in [0])
        ),
        clock=clock,
    )
    world.pdp = a2
    assert (
        world.gw.execute(action_request(world, proposal(key_suffix="a")))["error"]["code"]
        == "policy.requires_approval"
    )
    cases = [
        (lambda d: approve(d, params_hash="sha256:" + "0" * 64), "validation.binding"),
        (lambda d: approve(d, decision_id="dec_other"), "validation.binding"),
        (lambda d: approve(d, expires=ac.to_rfc3339(NOW - 1)), "validation.expired"),
        (lambda d: approve(d, signer="agent:field-station"), "validation.approval_signature"),
        (
            lambda d: approve(d, approver="owner:root"),
            "validation.approval_signature",
        ),  # ops key claiming root
    ]
    for i, (make, code) in enumerate(cases):
        rec = world.gw.execute(action_request(world, proposal(key_suffix=f"b{i}"), approval=make))
        assert rec["error"]["code"] == code, (i, rec)
    ok = world.gw.execute(action_request(world, proposal(key_suffix="good"), approval=approve))
    assert ok["status"] == "succeeded" and world.adapter.calls


def test_obligations_capability_suspend(world):
    r = action_request(world, proposal(key_suffix="u"))
    r["decision"]["obligations"].append({"type": "teleport"})
    r["decision"] = ac.sign_object(r["decision"], "decision", key("svc:pdp"))
    assert world.gw.execute(r)["error"]["code"] == "validation.obligation"
    other = proposal(key_suffix="cap")
    other["action"]["type"] = "ha.helper.set"
    gw = world.make(adapters=[MockAdapter("adapter:x", {"x.y"})])
    assert gw.execute(action_request(world, other))["error"]["code"] == "validation.capability"
    world.state["suspended"] = True
    assert (
        world.gw.execute(action_request(world, proposal(key_suffix="s")))["error"]["code"]
        == "policy.suspended"
    )
    dry = action_request(world, proposal(key_suffix="dry"))
    dry["decision"]["obligations"].append({"type": "dry_run"})
    dry["decision"] = ac.sign_object(dry["decision"], "decision", key("svc:pdp"))
    world.state["suspended"] = False
    rec = world.gw.execute(dry)
    assert (
        rec["status"] == "succeeded"
        and rec["adapter_response"] == {"dry_run": True}
        and world.adapter.calls == []
    )


def test_it15_rate_limit_and_breaker(world, clock):
    run = lambda suffix, **kw: world.gw.execute(action_request(world, proposal(key_suffix=suffix, **kw)))  # noqa: E731
    assert run("1")["status"] == "succeeded" and run("2")["status"] == "succeeded"
    third = run("3")
    assert third["error"]["code"] == "policy.rate_limited"  # third automatic action per target per hour
    owner = run("o", proposer="owner:ops")
    assert owner["status"] in ("succeeded", "rejected")
    clock.now += 3601
    world.adapter.fail_next = True
    assert run("4")["status"] == "failed"
    assert run("5")["error"]["code"] == "policy.circuit_open"  # no second failure in 24 h
    clock.now += 24 * 3600 + 1
    assert run("6")["status"] == "succeeded"


def test_daily_rate_limit_obligation(world, clock):
    for i in range(4):
        clock.now += 3601
        assert world.gw.execute(action_request(world, proposal(key_suffix=f"d{i}")))["status"] == "succeeded"
    clock.now += 3601
    rec = world.gw.execute(action_request(world, proposal(key_suffix="d5"), count_today=0))
    assert rec["error"]["code"] == "policy.rate_limited" and "max 4" in rec["error"]["message"]


def test_crash_reconciliation(world, tmp_path):
    world.adapter.crash_next = True
    r = action_request(world, proposal(key_suffix="crash"))
    with pytest.raises(SystemExit):
        world.gw.execute(r)
    # the adapter remembers the write: the restart settles it as done, without re-running
    reborn = world.make()
    settled = reborn.reconcile()
    assert len(settled) == 1 and settled[0]["status"] == "succeeded" and settled[0]["reconciled"]
    assert reborn.execute(r)["status"] == "succeeded" and len(world.adapter.calls) == 1
    # an adapter that cannot tell: recorded unknown, audited, never retried silently
    world.adapter.crash_next = True
    r2 = action_request(world, proposal(key_suffix="crash2"))
    with pytest.raises(SystemExit):
        reborn.execute(r2)
    forgetful = MockAdapter("adapter:ha@field-station", {"ha.helper.set"})
    again = world.make(adapters=[forgetful])
    [rec] = again.reconcile()
    assert rec["status"] == "unknown" and rec["error"]["code"] == "internal.unknown_outcome"
    assert list(world.audit.records())[-1]["kind"] == "action.unknown"
    assert again.execute(r2)["status"] == "unknown" and forgetful.calls == []
    assert again.reconcile() == []


def test_async_adapter(world):
    class Slow(MockAdapter):
        def execute(self, action_id, action):
            self.calls.append((action_id, dict(action)))
            return AdapterResult("started", {"queued": True})

        def reconcile(self, action_id, action):
            return None

    slow = Slow("adapter:host@field-station", {"ha.helper.set"})
    gw = world.make(adapters=[slow])
    r = action_request(world, proposal(key_suffix="async"))
    assert gw.execute(r)["status"] == "started"
    assert world.make(adapters=[slow]).reconcile() == []  # still running, not "unknown"
    done = gw.complete(r["action_id"], AdapterResult("succeeded", {"exit": 0}))
    assert done["status"] == "succeeded" and gw.execute(r)["status"] == "succeeded"
    with pytest.raises(Reject):
        gw.complete("act_nope", AdapterResult("succeeded"))


def test_adapter_exception_is_a_failure(world):
    class Broken(MockAdapter):
        def execute(self, action_id, action):
            raise RuntimeError("socket closed")

    gw = world.make(adapters=[Broken("adapter:ha@field-station", {"ha.helper.set"})])
    rec = gw.execute(action_request(world, proposal(key_suffix="boom")))
    assert rec["status"] == "failed" and "socket closed" in rec["error"]["message"]
