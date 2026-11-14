"""Node mesh (ADR-018): signed status, signed requests carrying the owner's code."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from autonomy_core import peer
from autonomy_core.errors import Reject
from autonomy_core.keys import KeyRing, encode_public_key, generate_private_key

SPEC = Path(__file__).resolve().parents[3] / "spec"
EDGE, PI = "cell:edge-1", "cell:pi-1"
NOW = 1_800_000_000.0


def conforms(kind, obj):
    sys.path.insert(0, str(SPEC))
    from autonomy_conformance import checks

    sys.path.remove(str(SPEC))
    errors = [f for f in checks.validate(kind, obj, "$", checks.Context(keys={})) if f.level == "error"]
    assert not errors, errors


@pytest.fixture
def keys():
    k = {EDGE: generate_private_key(), PI: generate_private_key()}
    ring = KeyRing({n: encode_public_key(v) for n, v in k.items()})
    return k, ring


class Clock:
    def __init__(self, t=NOW):
        self.t = t

    def __call__(self):
        return self.t


def guard(keys, codes=("123456",), grants=None, clock=None, cooldown=300):
    k, ring = keys
    used = set()

    def verify(code, now):
        if code in codes and code not in used:
            used.add(code)  # single use, as the bot's TOTP gate
            return True
        return False

    return peer.RequestGuard(
        me=EDGE,
        keys=ring,
        grants=grants
        if grants is not None
        else {PI: peer.Grant(frozenset({"restart_service", "reboot_host"}), frozenset({"frigate"}))},
        verify_code=verify,
        clock=clock or Clock(),
        cooldown_s=cooldown,
    )


def request(keys, **kw):
    args = {
        "sender": PI,
        "target": EDGE,
        "action": "restart_service",
        "key": keys[0][PI],
        "totp": "123456",
        "service": "frigate",
        "now": NOW,
    }
    args.update(kw)
    return peer.make_request(**args)


# --------------------------------------------------------------------------- requests


def test_a_valid_request_conforms_and_is_allowed(keys):
    req = request(keys)
    conforms("peer-request", req)
    assert guard(keys).check(req) == ("restart_service", "frigate")


def test_reboot_is_allowed_with_a_grant_and_a_code(keys):
    req = request(keys, action="reboot_host", service=None)
    conforms("peer-request", req)
    assert guard(keys).check(req) == ("reboot_host", None)


def test_no_code_a_wrong_code_and_a_reused_code_are_refused(keys):
    g = guard(keys)
    with pytest.raises(Reject, match="bad code"):
        g.check(request(keys, totp="000000"))
    ok = request(keys)
    assert g.check(ok)
    with pytest.raises(Reject):  # the same code again (a new nonce, so not the replay check)
        guard_reused = g
        guard_reused.cooldown_s = 0
        guard_reused.check(request(keys))


def test_a_valid_signature_is_not_enough_without_a_code(keys):
    """A compromised peer holds a good key but cannot produce the owner's code."""
    with pytest.raises(Reject, match="bad code"):
        guard(keys, codes=()).check(request(keys))


def test_a_replayed_envelope_is_refused(keys):
    g = guard(keys, codes=("123456", "654321"), cooldown=0)
    req = request(keys)
    g.check(req)
    with pytest.raises(Reject, match="replayed"):
        g.check(req)


def test_a_request_for_another_node_old_or_forged_is_refused(keys):
    g = guard(keys)
    with pytest.raises(Reject, match="wrong target"):
        g.check(request(keys, target="cell:other"))
    with pytest.raises(Reject, match="stale"):
        g.check(request(keys, now=NOW - 1000))
    with pytest.raises(Reject, match="bad signature"):
        g.check(request(keys, key=generate_private_key()))
    tampered = dict(request(keys), service="mosquitto")
    with pytest.raises(Reject, match="bad signature"):
        g.check(tampered)


def test_a_request_outside_the_grant_is_refused_before_the_code_is_checked(keys):
    codes = ("123456",)
    g = guard(
        keys, codes=codes, grants={PI: peer.Grant(frozenset({"restart_service"}), frozenset({"frigate"}))}
    )
    with pytest.raises(Reject, match="not granted"):
        g.check(request(keys, action="reboot_host", service=None))
    with pytest.raises(Reject, match="not granted"):
        g.check(request(keys, service="mosquitto"))
    assert g.check(request(keys))  # the code was not burnt by the refusals
    with pytest.raises(Reject, match="not granted"):
        guard(keys, grants={}).check(request(keys))


def test_the_cooldown_stops_a_second_run(keys):
    clock = Clock()
    g = guard(keys, codes=("111111", "222222", "333333"), clock=clock, cooldown=300)
    g.check(request(keys, totp="111111"))
    with pytest.raises(Reject, match="cooldown"):
        g.check(request(keys, totp="222222"))
    clock.t += 301
    assert g.check(request(keys, totp="333333", now=clock.t))
