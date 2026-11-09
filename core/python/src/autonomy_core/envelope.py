"""Building, signing and accepting envelopes (spec/schemas/v0/envelope.schema.json).

A receiver accepts an envelope only if it is well formed, signed by a known key,
fresh, and not seen before — checked in that order, so an unsigned flood cannot fill
the replay store::

    env = make_envelope(key, source="cell:hub", type="health.heartbeat", data={...},
                        seq=counter.next(), ttl=180)
    sender = verify_envelope(env, keyring, seen=seen_store)
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .errors import Reject
from .ids import (
    is_component_ref,
    new_correlation_id,
    new_traceparent,
    new_ulid,
    parse_rfc3339,
    to_rfc3339,
)
from .keys import Ed25519PrivateKey, KeyRing
from .replay import SeenStore
from .signing import sign_object, verify_object

SPECVERSION = "1.0"
REQUIRED = (
    "specversion",
    "id",
    "source",
    "type",
    "time",
    "seq",
    "correlationid",
    "traceparent",
    "principal",
    "trust",
    "privacy",
    "autonomysig",
)
TRUST = ("trusted", "owner", "untrusted")
PRIVACY = ("public", "site", "private", "secret-adjacent")
MAX_SEQ = 2**53 - 1


@dataclass(frozen=True)
class Freshness:
    """How old and how long-lived an accepted envelope may be.

    ``max_lifetime``: expires - time may not exceed it (None: no limit).
    ``future_slack``: seconds an envelope's time may be ahead of our clock.
    ``max_age``: for envelopes without ``expires``, how long after ``time`` they are accepted.
    ``require_expires``: commands should set it.
    """

    max_lifetime: float | None = None
    future_slack: float = 120.0
    max_age: float = 600.0
    require_expires: bool = False


DEFAULT_FRESHNESS = Freshness()


def make_envelope(
    key: Ed25519PrivateKey,
    *,
    source: str,
    type: str,  # noqa: A002 - the attribute is called type
    data: Any,
    seq: int,
    now: float | None = None,
    ttl: float | None = None,
    subject: str | None = None,
    dataschema: str | None = None,
    correlationid: str | None = None,
    causationid: str | None = None,
    traceparent: str | None = None,
    principal: str | None = None,
    trust: str = "trusted",
    privacy: str = "site",
    extensions: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """A signed envelope. ``ttl`` sets ``expires`` = now + ttl."""
    t = time.time() if now is None else now
    env: dict[str, Any] = {
        "specversion": SPECVERSION,
        "id": new_ulid(t),
        "source": source,
        "type": type,
    }
    if subject is not None:
        env["subject"] = subject
    env["time"] = to_rfc3339(t)
    env["datacontenttype"] = "application/json"
    if dataschema is not None:
        env["dataschema"] = dataschema
    env["data"] = data
    env["seq"] = seq
    if ttl is not None:
        env["expires"] = to_rfc3339(t + ttl)
    env["correlationid"] = correlationid or new_correlation_id(t)
    if causationid is not None:
        env["causationid"] = causationid
    env["traceparent"] = traceparent or new_traceparent()
    env["principal"] = principal or source
    env["trust"] = trust
    env["privacy"] = privacy
    for name, value in (extensions or {}).items():
        if name in env or name == "autonomysig":
            raise ValueError(f"extension {name} would overwrite an attribute")
        env[name] = value
    return sign_object(env, "envelope", key)


def is_envelope(message: Any) -> bool:
    return isinstance(message, dict) and "specversion" in message


def envelope_times(env: Mapping[str, Any]) -> tuple[float, float | None]:
    """(time, expires) as epoch seconds. Raises Reject("malformed")."""
    try:
        sent = parse_rfc3339(env["time"])
        expires = parse_rfc3339(env["expires"]) if "expires" in env else None
    except (KeyError, ValueError, TypeError):
        raise Reject("malformed", "time or expires") from None
    return sent, expires


def check_structure(env: Any) -> None:
    """The checks a receiver needs before trusting any field. Full schema validation is
    the conformance kit's job; this is what the library relies on."""
    if not isinstance(env, dict):
        raise Reject("malformed", "not an object")
    missing = [k for k in REQUIRED if k not in env]
    if missing:
        raise Reject("malformed", "missing " + ", ".join(missing))
    if env["specversion"] != SPECVERSION:
        raise Reject("unknown protocol version", str(env["specversion"])[:20])
    for name in ("id", "type", "correlationid", "traceparent", "autonomysig"):
        if not isinstance(env[name], str) or not env[name]:
            raise Reject("malformed", name)
    for name in ("source", "principal"):
        if not isinstance(env[name], str) or not is_component_ref(env[name]):
            raise Reject("malformed", name)
    seq = env["seq"]
    if not isinstance(seq, int) or isinstance(seq, bool) or not 0 <= seq <= MAX_SEQ:
        raise Reject("malformed", "seq")
    if env["trust"] not in TRUST or env["privacy"] not in PRIVACY:
        raise Reject("malformed", "trust or privacy")
    if "data_base64" in env:
        raise Reject("malformed", "data_base64 is not supported")
    sent, expires = envelope_times(env)
    if expires is not None and expires <= sent:
        raise Reject("malformed", "expires is not after time")


def check_fresh(env: Mapping[str, Any], now: float, freshness: Freshness = DEFAULT_FRESHNESS) -> float:
    """Raise Reject unless the envelope is fresh; return the time until which it must be
    remembered for replay protection."""
    sent, expires = envelope_times(env)
    if sent > now + freshness.future_slack:
        raise Reject("from the future", "clock difference?")
    if expires is None:
        if freshness.require_expires:
            raise Reject("malformed", "expires is required")
        until = sent + freshness.max_age
        if until <= now:
            raise Reject("expired")
        return until
    if expires <= sent:
        raise Reject("malformed", "expires is not after time")
    if expires <= now:
        raise Reject("expired")
    if freshness.max_lifetime is not None and expires - sent > freshness.max_lifetime:
        raise Reject("lifetime too long")
    return expires


def verify_envelope(
    env: Any,
    keys: KeyRing,
    *,
    now: float | None = None,
    freshness: Freshness = DEFAULT_FRESHNESS,
    seen: SeenStore | None = None,
) -> str:
    """Accept or Reject an envelope; return its source.

    Order: structure, signature, freshness, replay. ``seen`` remembers (source, id) until
    the envelope could no longer be accepted anyway.
    """
    t = time.time() if now is None else now
    check_structure(env)
    source = verify_object(env, "envelope", keys)
    until = check_fresh(env, t, freshness)
    if seen is not None and not seen.add(f"{source} {env['id']}", until, t):
        raise Reject("replayed")
    return source
