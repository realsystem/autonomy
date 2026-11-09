"""RFC 8785 JSON Canonicalization Scheme: the bytes every signature and hash covers.

Same algorithm as spec/autonomy_conformance/jcs.py (the reference); kept separate so the
runtime library does not depend on the conformance kit. spec/vectors/jcs/ keeps them equal.
"""

from __future__ import annotations

import json
import math
from decimal import Decimal
from typing import Any

MAX_SAFE_INT = 2**53 - 1


class CanonicalizationError(ValueError):
    """The value cannot be canonicalized (NaN, huge integer, non-string key, ...)."""


def canonicalize(value: Any) -> bytes:
    """The RFC 8785 canonical UTF-8 encoding of a JSON value."""
    return _serialize(value).encode("utf-8")


def _serialize(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        if abs(value) > MAX_SAFE_INT:
            raise CanonicalizationError(f"integer {value} is outside the IEEE 754 safe range")
        return str(value)
    if isinstance(value, float):
        return _number(value)
    if isinstance(value, str):
        return _string(value)
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_serialize(v) for v in value) + "]"
    if isinstance(value, dict):
        for k in value:
            if not isinstance(k, str):
                raise CanonicalizationError(f"object key {k!r} is not a string")
        keys = sorted(value, key=lambda k: k.encode("utf-16-be"))
        return "{" + ",".join(_string(k) + ":" + _serialize(value[k]) for k in keys) + "}"
    raise CanonicalizationError(f"{type(value).__name__} is not a JSON type")


def _string(s: str) -> str:
    if any(0xD800 <= ord(ch) <= 0xDFFF for ch in s):
        raise CanonicalizationError("string contains a lone surrogate")
    # ensure_ascii=False escapes exactly as ECMAScript JSON.stringify does.
    return json.dumps(s, ensure_ascii=False)


def _number(x: float) -> str:
    """ECMAScript Number.prototype.toString for a finite double."""
    if math.isnan(x) or math.isinf(x):
        raise CanonicalizationError("NaN and Infinity are not JSON numbers")
    if x == 0:
        return "0"
    sign = "-" if x < 0 else ""
    _, digits_t, exp = Decimal(repr(abs(x))).as_tuple()
    assert isinstance(exp, int)
    raw = "".join(map(str, digits_t))
    digits = raw.rstrip("0") or "0"
    k = len(digits)
    n = len(raw) + exp  # value = 0.<digits> * 10**n
    if k <= n <= 21:
        out = digits + "0" * (n - k)
    elif 0 < n <= 21:
        out = digits[:n] + "." + digits[n:]
    elif -6 < n <= 0:
        out = "0." + "0" * (-n) + digits
    else:
        e = n - 1
        out = digits[0] + ("." + digits[1:] if k > 1 else "") + "e" + ("+" if e >= 0 else "-") + str(abs(e))
    return sign + out
