"""C05 (M6): IT-12 approval binding and expiry; ST-08 suspend/hold work offline and stick."""

import json
import sys
from pathlib import Path

import pytest

import autonomy_core as ac
from autonomy_core import ApprovalService, ControlState, Reject

sys.path.insert(0, str(Path(__file__).resolve().parent))
from autonomy_core.gateway import ActionGateway, MockAdapter  # noqa: E402
from autonomy_core.pdp import PolicyBundle  # noqa: E402
from test_pdp_gateway import (  # noqa: E402
    FIX,
    NOW,
    PDP_KEYS,
    SPEC,
    TK,
    Clock,
    action_request,
    key,
    load,
    pdp,
    proposal,
    request,
)

OWNER = 1001


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def setup(tmp_path, clock):
    k = key("svc:approval")
    svc = ApprovalService(
        tmp_path, key=k, attester="svc:approval@field-station", operators=[OWNER], clock=clock
    )
    ctl = ControlState(
        tmp_path, key=k, attester="svc:approval@field-station", cell="field-station", clock=clock
    )
    adapter = MockAdapter("adapter:ha@field-station", {"ha.helper.set"})
    gw = ActionGateway(
        executor="svc:action-gateway@field-station",
        key=key("svc:action-gateway"),
        state_dir=tmp_path / "gw",
        pdp_keys=PDP_KEYS,
        registry=None,
        adapters=[adapter],
        clock=clock,
        blocked=ctl.blocked,
        approval_keys=ac.KeyRing({"svc:approval": TK["public"]["svc:approval"]}),
    )
    a2 = PolicyBundle(
        "2026.11.09-1", (), (load(FIX / "grant" / "valid" / "farm-helpers.json"),)
    )  # A2: approve each

    class S:
        pass

    s = S()
    s.svc, s.ctl, s.gw, s.adapter, s.pdp, s.tmp = svc, ctl, gw, adapter, pdp(a2, clock), tmp_path
    return s


def ask(s, p=None):
    p = p or proposal(key_suffix=ac.new_ulid())
    d = s.pdp.decide(request(p))
    assert d["effect"] == "require_approval"
    return p, d, s.svc.request(p, d, "set climate factor 104 -> 118")


def run(s, p, d, approval):
    return s.gw.execute(
        {
            "action_id": ac.prefixed_id("act"),
            "proposal": p,
            "decision": d,
            "approval": approval,
            "idempotency_key": p["idempotency_key"],
        }
    )


def test_approved_request_executes(setup):
    p, d, q = ask(setup)
    assert [r["request_id"] for r in setup.svc.pending()] == [q["request_id"]]
    _, approval = setup.svc.approve(q["request_id"], operator=OWNER, private_chat=True, code_ok=True)
    assert (
        approval["attested_by"] == "svc:approval@field-station"
        and approval["params_hash"] == d["params_hash"]
    )
    assert run(setup, p, d, approval)["status"] == "succeeded"
    assert setup.svc.pending() == []  # answered once
    sys.path.insert(0, str(SPEC))
    from autonomy_conformance import checks

    errors = [
        f
        for f in checks.validate("approval", approval, "$", checks.Context(keys=TK["public"]))
        if f.level == "error"
    ]
    assert not errors, errors


@pytest.mark.parametrize(
    "who,private,code,error",
    [
        (2002, True, True, "validation.operator"),  # wrong operator
        (OWNER, False, True, "validation.chat"),  # group chat
        (OWNER, True, False, "validation.code"),  # bad code
    ],
)
def test_it12_who_and_where(setup, who, private, code, error):
    _, _, q = ask(setup)
    with pytest.raises(Reject) as why:
        setup.svc.approve(q["request_id"], operator=who, private_chat=private, code_ok=code)
    assert why.value.reason == error
    assert setup.svc.pending()  # still pending: a failed attempt does not consume it


def test_it12_parameter_change_invalidates(setup):
    p, d, q = ask(setup)
    _, approval = setup.svc.approve(q["request_id"], operator=OWNER, private_chat=True, code_ok=True)
    changed = json.loads(json.dumps(p))
    changed["action"]["params"]["value"] = 150
    d2 = setup.pdp.decide(request(changed))
    rec = run(setup, changed, d2, approval)  # the old approval with a new decision
    assert rec["error"]["code"] == "validation.binding"
    rec = run(setup, changed, d, approval)  # the old decision for new parameters
    assert rec["error"]["code"] == "validation.binding"


def test_it12_expiry(setup, clock):
    _, _, q = ask(setup)
    clock.now += 901
    with pytest.raises(Reject, match="not pending"):
        setup.svc.approve(q["request_id"], operator=OWNER, private_chat=True, code_ok=True)
    p, d, q = ask(setup)
    _, approval = setup.svc.approve(q["request_id"], operator=OWNER, private_chat=True, code_ok=True)
    clock.now += 901
    assert run(setup, p, d, approval)["error"]["code"] in ("validation.expired",)


def test_untrusted_attester_and_forgery(setup):
    p, d, q = ask(setup)
    _, approval = setup.svc.approve(q["request_id"], operator=OWNER, private_chat=True, code_ok=True)
    forged = ac.sign_object(
        dict(approval, attested_by="svc:verifier@field-station"), "approval", key("svc:verifier")
    )
    assert run(setup, p, d, forged)["error"]["code"] == "validation.approval_signature"
    tampered = dict(approval, expires=ac.to_rfc3339(NOW + 86400))
    assert run(setup, p, d, tampered)["error"]["code"] == "validation.approval_signature"


def test_requests_survive_restart_and_deny(setup, clock):
    _, _, q = ask(setup)
    again = ApprovalService(
        setup.tmp,
        key=key("svc:approval"),
        attester="svc:approval@field-station",
        operators=[OWNER],
        clock=clock,
    )
    assert again.pending()[0]["request_id"] == q["request_id"]
    assert again.deny(q["request_id"]) and again.pending() == [] and again.deny(q["request_id"]) is None


def test_attest_for_owner_command(setup):
    d = setup.pdp.decide(request(proposal(proposer="owner:ops"), risk_class="R2", owner_verified=True))
    assert d["effect"] == "require_approval" and "also from the owner" in d["reason"]
    a = setup.svc.attest(d, operator=OWNER, private_chat=True, code_ok=True)
    assert a["decision_id"] == d["decision_id"] and setup.svc.pending() == []


# --- ST-08: emergency controls -------------------------------------------------------


def test_hold_blocks_automatic_not_owner_and_sticks(setup, clock):
    order = setup.ctl.order("hold", "all", "storm coming")
    assert order["kind"] == "hold" and order["scope"] == "all"
    p = proposal(proposer="agent:field-station", key_suffix="h1")
    rec = setup.gw.execute(action_request_with(setup, p))
    assert rec["error"]["code"] == "policy.suspended" and "on hold" in rec["error"]["message"]
    assert setup.ctl.blocked("owner:ops") is None
    restarted = ControlState(
        setup.tmp, key=key("svc:approval"), attester="x", cell="field-station", clock=clock
    )
    assert restarted.blocked("agent:field-station")  # sticky across restarts
    restarted.order("resume", "all", "storm passed")
    assert (
        ControlState(setup.tmp, key=key("svc:approval"), attester="x", cell="field-station").blocked(
            "agent:field-station"
        )
        is None
    )


def test_suspend_scopes(setup):
    setup.ctl.order("suspend", "agent:field-station", "acting oddly")
    assert setup.ctl.blocked("agent:field-station@field-station")
    assert setup.ctl.blocked("agent:research") is None
    setup.ctl.order("suspend", "cell:field-station", "maintenance")
    assert setup.ctl.blocked("agent:research")
    setup.ctl.order("resume", "cell:field-station", "done")
    assert setup.ctl.blocked("agent:research") is None and setup.ctl.blocked("agent:field-station")
    with pytest.raises(Reject):
        setup.ctl.order("pause", "all", "x")


def test_orders_conform(setup):
    sys.path.insert(0, str(SPEC))
    from autonomy_conformance import checks

    for o in (setup.ctl.order("suspend", "agent:field-station", "x"), setup.ctl.order("resume", "all", "y")):
        assert not [
            f
            for f in checks.validate("control-order", o, "$", checks.Context(keys=TK["public"]))
            if f.level == "error"
        ]


def action_request_with(s, p):
    class W:
        pass

    w = W()
    w.pdp = s.pdp
    return action_request(w, p)
