"""Signature and hash rules shared by every Autonomy contract.

- Signature: Ed25519 over canonicalize(object without its signature field),
  encoded ed25519:<base64url, unpadded>.
- params_hash: sha256 over canonicalize(proposal.action), i.e. the object
  {"type", "target", "params"}.
- Audit hash: sha256(utf8(prev_hash) || canonicalize(record without hash and
  signature)), where prev_hash is the full "sha256:<hex>" string.

Only verification lives here. Signing belongs to the core library (C02); the
fixture generator in spec/tools/ signs with published test keys.
"""

from __future__ import annotations

import base64
import hashlib
from typing import Any, Mapping

from .jcs import canonicalize

GENESIS_HASH = "sha256:" + "0" * 64

# Object kind -> (signature field, field naming the signer).
SIGNED = {
    "envelope": ("autonomysig", "source"),
    "grant": ("signature", "issued_by"),
    "decision": ("signature", "issuer"),
    "approval": ("signature", "approver"),
    "execution-record": ("signature", "executor"),
    "verification": ("signature", "verifier"),
    "audit-record": ("signature", "actor"),
}


def b64url_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def b64url_encode(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def sha256_tagged(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def without(obj: Mapping[str, Any], *fields: str) -> dict:
    return {k: v for k, v in obj.items() if k not in fields}


def signing_input(obj: Mapping[str, Any], sig_field: str) -> bytes:
    return canonicalize(without(obj, sig_field))


def params_hash(action: Mapping[str, Any]) -> str:
    return sha256_tagged(canonicalize(dict(action)))


def audit_hash(record: Mapping[str, Any]) -> str:
    body = canonicalize(without(record, "hash", "signature"))
    return sha256_tagged(record["prev_hash"].encode("utf-8") + body)


def lookup_key(keys: Mapping[str, str], signer: str) -> str | None:
    """Find a signer's key: exact reference first, then the component without @cell."""
    if signer in keys:
        return keys[signer]
    base = signer.split("@", 1)[0]
    return keys.get(base)


def verify(obj: Mapping[str, Any], kind: str, keys: Mapping[str, str]) -> str | None:
    """Return None when the signature verifies, otherwise a reason."""
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError:  # pragma: no cover - dependency declared in pyproject
        return "the 'cryptography' package is required to verify signatures"

    sig_field, signer_field = SIGNED[kind]
    signer = obj.get(signer_field)
    sig = obj.get(sig_field)
    if not isinstance(signer, str) or not isinstance(sig, str):
        return f"missing {signer_field} or {sig_field}"
    key = lookup_key(keys, signer)
    if key is None:
        return f"no public key for signer {signer}"
    try:
        pub = Ed25519PublicKey.from_public_bytes(b64url_decode(key.split(":", 1)[1]))
        pub.verify(b64url_decode(sig.split(":", 1)[1]), signing_input(obj, sig_field))
    except InvalidSignature:
        return f"{sig_field} does not verify against the key of {signer}"
    except (ValueError, IndexError) as e:
        return f"malformed key or signature: {e}"
    return None
