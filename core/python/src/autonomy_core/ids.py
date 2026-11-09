"""Identifiers and timestamps in the forms the contracts require."""

from __future__ import annotations

import os
import re
import secrets
import time
from datetime import datetime, timezone

CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_COMPONENT_REF = re.compile(
    r"^(?:(?:cell|svc|agent|adapter|app):[a-z0-9]+(?:-[a-z0-9]+)*(?:@[a-z0-9]+(?:-[a-z0-9]+)*)?"
    r"|owner:(?:root|ops))$"
)


def new_ulid(now: float | None = None) -> str:
    """A ULID: 48-bit millisecond time, 80 random bits, Crockford base32."""
    ms = int((time.time() if now is None else now) * 1000)
    value = (ms << 80) | int.from_bytes(os.urandom(10), "big")
    return "".join(CROCKFORD[(value >> (5 * i)) & 31] for i in range(25, -1, -1))


def prefixed_id(prefix: str, now: float | None = None) -> str:
    """e.g. prefixed_id("dec") -> "dec_01JC9Q4..."."""
    return f"{prefix}_{new_ulid(now)}"


def new_correlation_id(now: float | None = None) -> str:
    return prefixed_id("corr", now)


def new_traceparent() -> str:
    """A fresh W3C traceparent (version 00, sampled)."""
    return f"00-{secrets.token_hex(16)}-{secrets.token_hex(8)}-01"


def child_traceparent(parent: str) -> str:
    """Same trace, new span."""
    parts = parent.split("-")
    if len(parts) != 4:
        return new_traceparent()
    return f"00-{parts[1]}-{secrets.token_hex(8)}-{parts[3]}"


def is_name(text: str) -> bool:
    """Lowercase letters, digits and single hyphens (cell and component names)."""
    return len(text) <= 63 and bool(_NAME.match(text))


def is_component_ref(text: str) -> bool:
    return len(text) <= 192 and bool(_COMPONENT_REF.match(text))


def to_rfc3339(epoch: float) -> str:
    """UTC with a Z suffix; milliseconds only when the time has a fraction."""
    dt = datetime.fromtimestamp(epoch, tz=timezone.utc)
    if dt.microsecond:
        return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_rfc3339(text: str) -> float:
    """Epoch seconds of an RFC 3339 time. Raises ValueError."""
    if not isinstance(text, str) or not text.endswith("Z"):
        raise ValueError("time must be RFC 3339 UTC with a Z suffix")
    body = text[:-1]
    if "." in body:  # fromisoformat before 3.11 accepts only 3 or 6 fraction digits
        head, frac = body.split(".", 1)
        if not frac.isdigit() or not 1 <= len(frac) <= 9:
            raise ValueError("bad fractional seconds")
        body = f"{head}.{(frac + '000000')[:6]}"
    return datetime.fromisoformat(body).replace(tzinfo=timezone.utc).timestamp()
