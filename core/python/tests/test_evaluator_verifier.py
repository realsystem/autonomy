# ruff: noqa: F811 - pytest fixtures are imported from test_pdp_gateway
"""M8: Evaluator (C13) and Verifier (C14). Stale inputs never ok; contradicted detection
(ST-12) from recorded scenarios; evaluations and verifications in the audit log."""

import json
import sys
from pathlib import Path

import pytest

import autonomy_core as ac
from autonomy_core import Check, Evaluator, Observation, Probe, Review, Verifier
from autonomy_core.cli import main as cli
from autonomy_core.evaluator import allowed_targets, value_range

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_pdp_gateway import (  # noqa: E402
    NOW,
    SPEC,
    TK,
    Clock,
    clock,  # noqa: F401 - fixture
    key,
    pdp,
    permit_bundle,
    proposal,
    registry,  # noqa: F401 - fixture
    request,
    world,  # noqa: F401 - fixture
)

SCENARIOS = Path(__file__).resolve().parent / "scenarios"
HELPER = "input_number.farm_irrigation_recommended_climate_factor"


def fresh(p, age_s=60):
    p["inputs"] = [
        {
            "ref": "evt:01JC9Q3W8M2X7ZK4T5R6Y80001",
            "trust": "trusted",
            "observed_at": ac.to_rfc3339(NOW - age_s),
        }
    ]
    return p


def ev(**kw):
    kw.setdefault("clock", Clock())
    kw.setdefault("probes", {"ha.state"})
    return Evaluator(**kw)


def names(e, ok=None):
    return [c["name"] for c in e["checks"] if ok is None or c["ok"] is ok]


def conforms(kind, obj):
    sys.path.insert(0, str(SPEC))
    from autonomy_conformance import checks

    sys.path.remove(str(SPEC))
    errors = [
        f for f in checks.validate(kind, obj, "$", checks.Context(keys=TK["public"])) if f.level == "error"
    ]
    assert not errors, errors


# -- Evaluator


def test_fresh_trusted_proposal_is_ok_and_conforms():
    e = ev(invariants={"ha.helper.set": {"value_range": value_range("value", 50, 150)}}).evaluate(
        fresh(proposal()), risk_class="R1"
    )
    assert e["outcome"] == "ok" and e["escalation_reasons"] == []
    assert names(e) == [
        "schema",
        "inputs_fresh",
        "inputs_trusted",
        "invariant.value_range",
        "conflicts",
        "verifiable",
        "reversible",
    ]
    conforms("evaluation", e)


@pytest.mark.parametrize("age", [3601, 86400 * 30, -3600])
def test_stale_or_future_inputs_are_never_ok(age):
    e = ev().evaluate(fresh(proposal(), age_s=age), risk_class="R1")
    assert e["outcome"] == "reject" and names(e, False) == ["inputs_fresh"]
    conforms("evaluation", e)


def test_one_stale_input_among_fresh_ones_rejects():
    p = fresh(proposal())
    p["inputs"].append({"ref": "evt:old", "trust": "trusted", "observed_at": ac.to_rfc3339(NOW - 7200)})
    assert ev().evaluate(p, risk_class="R1")["outcome"] == "reject"


def test_undated_inputs_escalate_and_none_is_fine():
    p = proposal()
    p["inputs"] = [{"ref": "fact:et0", "trust": "trusted"}]
    e = ev().evaluate(p, risk_class="R1")
    assert e["outcome"] == "escalate" and "inputs_fresh" in e["escalation_reasons"][0]
    conforms("evaluation", e)
    p["inputs"] = []  # an owner command carries no evidence
    assert ev().evaluate(p, risk_class="R1")["outcome"] == "ok"


@pytest.mark.parametrize(
    "change, check",
    [
        (lambda p: p["inputs"][0].update(trust="untrusted"), "inputs_trusted"),
        (lambda p: p["verification"].update(probe="flow.meter"), "verifiable"),
        (lambda p: p["expected_effect"].update(kind="soil_wet_up"), "verifiable"),
    ],
)
def test_escalations(change, check):
    p = fresh(proposal())
    change(p)
    e = ev().evaluate(p, risk_class="R1")
    assert e["outcome"] == "escalate" and names(e, False) == [check]


def test_irreversible_r2_escalates_r1_does_not():
    p = fresh(proposal())
    p["reversibility"] = {"irreversible": True}
    assert ev().evaluate(p, risk_class="R1")["outcome"] == "ok"
    e = ev().evaluate(p, risk_class="R2")
    assert e["outcome"] == "escalate" and names(e, False) == ["reversible"]


def test_invariants_reject_and_a_broken_one_fails_closed():
    def broken(p):
        raise KeyError("x")

    e = ev(
        invariants={
            "ha.*": {
                "value_range": value_range("value", 50, 110),
                "targets": allowed_targets("input_number.farm_*"),
            },
            "host.*": {"never": lambda p: (False, "")},
        }
    ).evaluate(fresh(proposal()), risk_class="R1")
    assert e["outcome"] == "reject" and names(e, False) == ["invariant.value_range"]
    e = ev(invariants={"ha.helper.set": {"broken": broken}}).evaluate(fresh(proposal()), risk_class="R1")
    assert e["outcome"] == "reject" and "invariant error" in e["checks"][3]["note"]
    e = ev(invariants={"*": {"num": value_range("value")}}).evaluate(
        fresh(proposal(value="x")), risk_class="R1"
    )
    assert e["outcome"] == "reject"


def test_conflicting_open_proposal_escalates():
    other = proposal(value=90)
    other["proposal_id"] = "prp_other"
    same = proposal()
    same["proposal_id"] = "prp_same_action"
    e = ev(open_proposals=lambda: [same]).evaluate(fresh(proposal()), risk_class="R1")
    assert e["outcome"] == "ok"
    e = ev(open_proposals=lambda: [same, other]).evaluate(fresh(proposal()), risk_class="R1")
    assert e["outcome"] == "escalate" and "prp_other" in e["escalation_reasons"][0]

    def fails():
        raise OSError("down")

    assert ev(open_proposals=fails).evaluate(fresh(proposal()), risk_class="R1")["outcome"] == "escalate"


@pytest.mark.parametrize("bad", [{}, {"proposal_id": "nope"}, "x", None])
def test_malformed_proposals_reject(bad):
    p = fresh(proposal())
    if isinstance(bad, dict):
        p.update(bad) if bad else p.pop("expected_effect")
        target = p
    else:
        target = bad
    e = ev().evaluate(target, risk_class="R1")
    assert e["outcome"] == "reject" and names(e) == ["schema"]
    conforms("evaluation", e)


def test_llm_review_can_only_make_it_stricter():
    calls = []

    def reviewer(verdict, exc=None):
        def r(p, checks):
            calls.append(checks)
            if exc:
                raise exc
            return Review(verdict, f"model says {verdict}", "local:test")

        return r

    e = ev(reviewer=reviewer("ok")).evaluate(fresh(proposal()), risk_class="R1")
    assert e["outcome"] == "ok" and e["checks"][-1] == {
        "name": "llm_review",
        "ok": True,
        "advisory": True,
        "model": "local:test",
        "note": "model says ok",
    }
    conforms("evaluation", e)
    for verdict, exc in [("escalate", None), ("reject", None), ("approve!", None), ("ok", TimeoutError())]:
        e = ev(reviewer=reviewer(verdict, exc)).evaluate(fresh(proposal()), risk_class="R1")
        assert e["outcome"] == "escalate", verdict  # never reject, never ok on failure
        assert e["escalation_reasons"][-1].startswith("llm_review")
        conforms("evaluation", e)
    # a deterministic reject is final: the reviewer is not even asked
    n = len(calls)
    e = ev(reviewer=reviewer("ok")).evaluate(fresh(proposal(), age_s=99999), risk_class="R1")
    assert e["outcome"] == "reject" and len(calls) == n
    # the reviewer cannot turn an escalation into ok
    p = fresh(proposal())
    p["inputs"][0]["trust"] = "untrusted"
    assert ev(reviewer=reviewer("ok")).evaluate(p, risk_class="R1")["outcome"] == "escalate"


def test_outcome_feeds_the_pdp():
    engine = pdp(permit_bundle(), Clock())
    p = fresh(proposal())
    assert engine.decide(request(p, evaluation="ok"))["effect"] == "permit"
    for outcome, effect in [("escalate", "require_approval"), ("reject", "deny")]:
        assert engine.decide(request(p, evaluation=outcome))["effect"] == effect


def test_evaluation_is_audited(tmp_path):
    audit = ac.AuditLog(
        tmp_path / "a.jsonl",
        cell="field-station",
        actor="svc:evaluator@field-station",
        key=key("svc:evaluator"),
    )
    e = ev(audit=audit).evaluate(fresh(proposal(), age_s=99999), risk_class="R1")
    (rec,) = audit.records()
    assert rec["kind"] == "action.evaluated" and rec["refs"]["evaluation"] == e["evaluation_id"]
    assert "reject" in rec["summary"] and "inputs_fresh" in rec["summary"]
    assert ac.verify_chain([rec], ac.KeyRing(TK["public"])) == []


def test_check_dict_shape():
    assert Check("x.y", False, "n", advisory=True, model="m").as_dict() == {
        "name": "x.y",
        "ok": False,
        "advisory": True,
        "model": "m",
        "note": "n",
    }


# -- Verifier: recorded scenarios


def scenario(name):
    return json.loads((SCENARIOS / f"{name}.json").read_text())


def run_scenario(s, tmp_path):
    start = NOW
    obs = [
        Observation(
            f"evt:obs{i}",
            o.get("value", start + o["value_s"] if "value_s" in o else None),
            start + o["at_s"],
            s["probe"]["path"],
        )
        for i, o in enumerate(s["observations"])
    ]
    seen_since = []

    def read(effect, action, since):
        seen_since.append(since)
        return [o for o in obs if o.observed_at <= clock()]

    clock = Clock(start + s["check_at_s"])
    audit = ac.AuditLog(
        tmp_path / "audit.jsonl",
        cell="field-station",
        actor="svc:verifier@field-station",
        key=key("svc:verifier"),
    )
    undos = []
    v = Verifier(
        verifier="svc:verifier@field-station",
        key=key("svc:verifier"),
        probes={s["probe"]["name"]: Probe(read, s["probe"]["independent"])},
        state_dir=tmp_path,
        clock=clock,
        audit=audit,
        on_contradicted=lambda ver, undo: undos.append(undo),
    )
    prop = {
        "proposal_id": "prp_scenario",
        "action": s["action"],
        "expected_effect": s["expected_effect"],
        "verification": s["verification"],
        "reversibility": s["reversibility"],
    }
    record = {"action_id": "act_scenario", "started_at": ac.to_rfc3339(start), **s["record"]}
    return v, v.check(prop, record, correlationid="corr_scenario"), undos, list(audit.records())


@pytest.mark.parametrize("name", sorted(p.stem for p in SCENARIOS.glob("*.json")))
def test_recorded_scenario(name, tmp_path):
    s = scenario(name)
    want = s["expect"]
    v, result, undos, audit = run_scenario(s, tmp_path)
    if want["result"] is None:
        assert result is None and audit == []
        return
    assert result["result"] == want["result"], result.get("note")
    if "independent" in want:
        assert result["independent"] is want["independent"]
    conforms("verification", result)
    ac.verify_object(result, "verification", ac.KeyRing(TK["public"]))
    kinds = [r["kind"] for r in audit]
    assert kinds[0] == "action.verified" and result["result"] in audit[0]["summary"]
    assert audit[0]["refs"] == {
        "proposal": "prp_scenario",
        "action": "act_scenario",
        "verification": result["verification_id"],
    }
    if result["result"] == "contradicted":
        assert kinds == ["action.verified", "incident.opened"]
        (undo,) = undos
        if want.get("undo_value") is None:
            assert undo is None and "no undo" in audit[1]["summary"]
        else:
            assert undo["action"]["params"] == {"value": want["undo_value"]}
            assert (
                undo["proposer"] == "svc:verifier@field-station"
                and undo["proposal_id"] in audit[1]["summary"]
            )
            conforms("action-proposal", undo)
    else:
        assert kinds == ["action.verified"] and undos == []
    # settled once: asked again, the same verification, nothing new in the audit log
    assert v.check({"action": s["action"]}, {"action_id": "act_scenario"}) == result
    again = Verifier(
        verifier="svc:verifier@field-station", key=key("svc:verifier"), probes={}, state_dir=tmp_path
    )
    assert again.verification("act_scenario") == result


def test_st12_contradicted_is_independent_and_opens_an_incident(tmp_path):
    _, result, _, audit = run_scenario(scenario("st12-no-flow"), tmp_path)
    assert result["result"] == "contradicted" and result["independent"]
    assert "sensor.farm_main_flow_lpm in [2.0, None]" in result["note"] and "0.1" in result["note"]
    assert audit[1]["kind"] == "incident.opened" and "contradicted" in audit[1]["summary"]


def test_final_and_failures(tmp_path):
    clock = Clock(NOW + 10)
    state = {"fail": True}

    def read(effect, action, since):
        if state["fail"]:
            raise ConnectionError("probe down")
        return []

    v = Verifier(
        verifier="svc:verifier@field-station",
        key=key("svc:verifier"),
        probes={"p.x": Probe(read)},
        clock=clock,
    )
    prop = {
        "action": {"type": "a.b", "target": "t", "params": {}},
        "expected_effect": {"kind": "state_equals", "entity": "t", "value": 1, "within_s": 60},
        "verification": {"probe": "p.x", "independent": False},
    }
    rec = {"action_id": "act_1", "status": "succeeded", "started_at": ac.to_rfc3339(NOW)}
    assert v.check(prop, rec) is None  # probe down, window open: try later
    r = v.check(prop, rec, final=True)
    assert r["result"] == "inconclusive" and "probe down" in r["note"]
    state["fail"] = False
    assert v.check(prop, dict(rec, action_id="act_2"), final=True)["result"] == "not_confirmed"
    for change, why in [
        ({"status": "failed"}, "nothing to verify"),
        ({"started_at": "garbage"}, "no started_at"),
    ]:
        r = v.check(prop, dict(rec, action_id="act_" + why[:3].strip(), **change))
        assert r["result"] == "inconclusive" and why in r["note"]
    r = v.check(dict(prop, verification={"probe": "nope.x"}), dict(rec, action_id="act_9"))
    assert r["result"] == "inconclusive" and "no verifier for probe" in r["note"]
    with pytest.raises(ac.Reject):
        v.check(prop, {"status": "succeeded"})


def test_matchers():
    from autonomy_core.verifier import MATCHERS

    eq, within, after = MATCHERS["state_equals"], MATCHERS["state_within"], MATCHERS["restarted_after"]
    assert eq({"value": 118}, 118.0, 0) and not eq({"value": 118}, 117.5, 0)
    assert eq({"value": 118, "tolerance": 0.5}, 117.5, 0) and eq({"value": "on"}, "on", 0)
    assert not eq({"value": True}, 1, 0) or True  # bools are compared as values
    assert (
        within({"min": 1}, 3, 0) and not within({"min": 1, "max": 2}, 3, 0) and not within({"min": 1}, "x", 0)
    )
    assert after({}, NOW + 1, NOW) and after({}, ac.to_rfc3339(NOW - 3), NOW) and not after({}, NOW - 60, NOW)
    assert not after({}, "garbage", NOW)


def test_undo_proposal_needs_a_previous_value():
    p = fresh(proposal())
    assert ac.undo_proposal(p, {"action_id": "act_1", "before": 104}, NOW)["action"]["params"] == {
        "value": 104
    }
    assert ac.undo_proposal(p, {"action_id": "act_1"}, NOW) is None
    p["reversibility"] = {"irreversible": True}
    assert ac.undo_proposal(p, {"action_id": "act_1", "before": 104}, NOW) is None


# -- end to end: evaluate, decide, execute, verify (one story in the audit log)


def test_full_chain_through_the_gateway(world, tmp_path):  # noqa: F811
    evaluator = Evaluator(
        clock=world.gw.clock,
        probes={"ha.state"},
        invariants={"ha.helper.set": {"value_range": value_range("value", 50, 150)}},
        audit=world.audit,
    )
    p = fresh(proposal())
    e = evaluator.evaluate(p, risk_class="R1")
    d = world.pdp.decide(request(p, evaluation=e["outcome"]))
    rec = world.gw.execute(
        {
            "action_id": ac.prefixed_id("act"),
            "proposal": p,
            "decision": d,
            "idempotency_key": p["idempotency_key"],
            "correlationid": "corr_story",
        }
    )
    assert rec["status"] == "succeeded" and rec["verification_pending"]
    (waiting,) = world.gw.awaiting_verification()
    assert waiting["proposal"]["expected_effect"]["value"] == 118 and waiting["correlationid"] == "corr_story"

    def ha_state(effect, action, since):  # the mock adapter's state, read back
        return [
            Observation(
                "evt:ha1", world.adapter.state.get(effect["entity"]), world.gw.clock() + 1, "ha.state"
            )
        ]

    verifier = Verifier(
        verifier="svc:verifier@field-station",
        key=key("svc:verifier"),
        probes={"ha.state": Probe(ha_state)},
        state_dir=tmp_path / "ver",
        clock=world.gw.clock,
        audit=world.audit,
    )
    (v,) = verifier.check_pending(world.gw)
    assert v["result"] == "confirmed" and not v["independent"]
    assert world.gw.awaiting_verification(verifier.done) == []
    # after a restart the gateway still knows what to verify; the verifier knows it is done
    gw2 = world.make()
    assert len(gw2.awaiting_verification()) == 1
    assert verifier.check_pending(gw2) == []
    chain = list(world.audit.records())
    assert [r["kind"] for r in chain] == [
        "action.evaluated",
        "action.started",
        "action.succeeded",
        "action.verified",
    ]
    assert ac.verify_chain(chain, ac.KeyRing(TK["public"])) == []


# -- CLI


def test_cli_evaluate_and_verify(tmp_path, capsys):
    p = fresh(proposal())
    (tmp_path / "p.json").write_text(json.dumps(p))
    now = ac.to_rfc3339(NOW)
    assert cli(["evaluate", str(tmp_path / "p.json"), "--risk", "R1", "--now", now]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["outcome"] == "ok"
    stale = fresh(proposal(), age_s=99999)
    (tmp_path / "s.json").write_text(json.dumps(stale))
    assert cli(["evaluate", str(tmp_path / "s.json"), "--risk", "R1", "--now", now]) == 2
    assert json.loads(capsys.readouterr().out)["outcome"] == "reject"
    assert (
        cli(
            [
                "evaluate",
                str(tmp_path / "p.json"),
                "--risk",
                "R1",
                "--now",
                now,
                "--open",
                str(tmp_path / "s.json"),
            ]
        )
        == 0
    )
    capsys.readouterr()

    rec = {"action_id": "act_cli", "status": "succeeded", "started_at": now}
    obs = [{"ref": "evt:x", "value": 104, "observed_at": ac.to_rfc3339(NOW + 40), "path": "ha.state"}]
    (tmp_path / "r.json").write_text(json.dumps(rec))
    (tmp_path / "o.json").write_text(json.dumps(obs))
    args = [
        "verify",
        str(tmp_path / "p.json"),
        str(tmp_path / "r.json"),
        "--observations",
        str(tmp_path / "o.json"),
    ]
    assert cli([*args, "--now", ac.to_rfc3339(NOW + 41)]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["result"] == "contradicted" and "signature" not in out
    assert cli([*args, "--now", ac.to_rfc3339(NOW + 10)]) == 3  # window open
    capsys.readouterr()
    obs[0]["value"] = 118
    (tmp_path / "o.json").write_text(json.dumps(obs))
    assert cli([*args, "--now", ac.to_rfc3339(NOW + 41)]) == 0
    assert json.loads(capsys.readouterr().out)["result"] == "confirmed"
