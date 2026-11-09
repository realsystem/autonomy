"""Signatures over contract objects, params_hash and the audit hash (spec/README.md)."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

from .errors import Reject
from .jcs import canonicalize
from .keys import Ed25519PrivateKey, KeyRing, sign, verify

GENESIS_HASH = "sha256:" + "0" * 64

# kind -> (signature field, signer field), as in spec/README.md#signatures-and-hashes
SIGNED: dict[str, tuple[str, str]] = {
    "envelope": ("autonomysig", "source"),
    "grant": ("signature", "issued_by"),
    "decision": ("signature", "issuer"),
    "approval": ("signature", "approver"),
    "execution-record": ("signature", "executor"),
    "verification": ("signature", "verifier"),
    "audit-record": ("signature", "actor"),
    "control-order": ("signature", "issued_by"),
}


def sha256_tagged(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def without(obj: Mapping[str, Any], *fields: str) -> dict[str, Any]:
    return {k: v for k, v in obj.items() if k not in fields}


def signing_input(obj: Mapping[str, Any], kind: str) -> bytes:
    return canonicalize(without(obj, SIGNED[kind][0]))


def sign_object(obj: dict[str, Any], kind: str, key: Ed25519PrivateKey) -> dict[str, Any]:
    """Set the signature field of ``obj`` (in place) and return it."""
    sig_field = SIGNED[kind][0]
    obj.pop(sig_field, None)
    obj[sig_field] = sign(key, signing_input(obj, kind))
    return obj


# Kinds an approval service (C05) may sign on the owner's behalf: attested_by is the signer.
ATTESTED = {"approval", "control-order"}


def verify_object(obj: Mapping[str, Any], kind: str, keys: KeyRing) -> str:
    """Return the signer reference, or raise Reject("unknown signer" | "bad signature")."""
    sig_field, signer_field = SIGNED[kind]
    if kind in ATTESTED and isinstance(obj.get("attested_by"), str):
        signer_field = "attested_by"
    signer = obj.get(signer_field)
    if not isinstance(signer, str):
        raise Reject("malformed", f"{signer_field} missing")
    key = keys.get(signer)
    if key is None:
        raise Reject("unknown signer", signer)
    if not verify(key, signing_input(obj, kind), str(obj.get(sig_field, ""))):
        raise Reject("bad signature", signer)
    return signer


def params_hash(action: Mapping[str, Any]) -> str:
    """sha256 of the canonical proposal.action ({"type", "target", "params"})."""
    return sha256_tagged(canonicalize(dict(action)))


def audit_hash(record: Mapping[str, Any]) -> str:
    body = canonicalize(without(record, "hash", "signature"))
    return sha256_tagged(str(record["prev_hash"]).encode("utf-8") + body)
