"""ops/lab/authority/make_authority.py: what the lab bot is given must verify, and the grant must
do what the lab scenario (ask-grant) relies on: permitted by policy, still waiting for the owner."""

from __future__ import annotations

import importlib.util
import json
import time
from pathlib import Path

import pytest

from autonomy_core import Pdp, decision_request
from autonomy_core.pdp import load_bundle
from autonomy_core.registry import RegistryStore

SCRIPT = Path(__file__).resolve().parents[3] / "ops" / "lab" / "authority" / "make_authority.py"


@pytest.fixture(scope="module")
def authority(tmp_path_factory):
    spec = importlib.util.spec_from_file_location("make_authority", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    out = tmp_path_factory.mktemp("authority")
    mod.main(out)
    return out


def test_registry_and_bundle_verify(authority):
    now = time.time()
    registry = RegistryStore(authority / "registry").current(now=now)
    assert registry is not None
    bundle = load_bundle(json.loads((authority / "policy-bundle.json").read_text()), registry, now=now)
    assert [g["grantee"] for g in bundle.grants] == ["agent:node-assistant"]
    assert bundle.grants[0]["autonomy_level"] == "A1"


def test_private_keys_are_private(authority):
    for name in ("owner-root.key", "owner-ops.key"):
        assert (authority / name).stat().st_mode & 0o077 == 0


def test_bundle_without_the_registry_is_refused(authority, tmp_path):
    doc = json.loads((authority / "policy-bundle.json").read_text())
    with pytest.raises(Exception, match="bad signature"):
        load_bundle(doc, None, now=time.time())


def test_assistant_restart_is_permitted_but_waits_for_the_owner(authority):
    """A1 covers no R1 action: the PDP says require_approval, so it goes to /pending."""
    now = time.time()
    registry = RegistryStore(authority / "registry").current(now=now)
    bundle = load_bundle(json.loads((authority / "policy-bundle.json").read_text()), registry, now=now)
    # Cedar is a separate engine (CI runs it for real); here it permits what the policy permits
    pdp = Pdp(
        issuer="svc:pdp@lab",
        key=__import__("autonomy_core").generate_private_key(),
        bundle=bundle,
        cedar=lambda request, policies, entities: (
            ("grant" in request["context"], ["policy0"], [])
            if "grant" in request["context"]
            else (False, [], [])
        ),
        clock=lambda: now,
    )
    proposal = {
        "proposal_id": "prp_1",
        "proposer": "agent:node-assistant",
        "objective": {"id": "obj", "text": "keep frigate up"},
        "action": {"type": "host.restart_service", "target": "frigate", "params": {}},
        "rationale": "stuck",
        "inputs": [],
        "expected_effect": {"kind": "service_running", "within_s": 300},
        "verification": {"probe": "host.service_state", "independent": False},
        "reversibility": {"irreversible": True},
        "freshness_s": 300,
        "idempotency_key": "k1",
    }
    out = pdp.evaluate(decision_request(proposal, cell="lab", risk_class="R1"))
    assert out.effect == "require_approval" and "level A1 needs approval" in out.reason
    other = dict(proposal, action={"type": "host.restart_service", "target": "mosquitto", "params": {}})
    assert pdp.evaluate(decision_request(other, cell="lab", risk_class="R1")).effect == "deny"
