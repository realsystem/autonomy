"""Ed25519 keys and signatures in the contract encoding ``ed25519:<base64url, unpadded>``."""

from __future__ import annotations

import base64
import binascii
from collections.abc import Iterable, Mapping
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

PREFIX = "ed25519:"

__all__ = [
    "Ed25519PrivateKey",
    "Ed25519PublicKey",
    "KeyRing",
    "b64url_decode",
    "b64url_encode",
    "decode_public_key",
    "encode_public_key",
    "generate_private_key",
    "load_private_key",
    "private_key_from_seed",
    "save_private_key",
    "sign",
    "verify",
]


def b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(text: str) -> bytes:
    if not isinstance(text, str) or "=" in text:
        raise ValueError("expected unpadded base64url")
    try:
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (binascii.Error, ValueError) as exc:
        raise ValueError("not base64url") from exc


def generate_private_key() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


def private_key_from_seed(seed: bytes) -> Ed25519PrivateKey:
    """For tests and vectors; production keys come from generate_private_key()."""
    return Ed25519PrivateKey.from_private_bytes(seed)


def load_private_key(path: str | Path) -> Ed25519PrivateKey:
    """An unencrypted PEM (PKCS#8) Ed25519 private key."""
    key = serialization.load_pem_private_key(Path(path).read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError(f"{path} is not an Ed25519 key")
    return key


def save_private_key(key: Ed25519PrivateKey, path: str | Path) -> None:
    """Write as PEM, readable by the owner only."""
    p = Path(path)
    pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    p.touch(mode=0o600, exist_ok=True)
    p.chmod(0o600)
    p.write_bytes(pem)


def encode_public_key(key: Ed25519PrivateKey | Ed25519PublicKey) -> str:
    pub = key.public_key() if isinstance(key, Ed25519PrivateKey) else key
    raw = pub.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return PREFIX + b64url_encode(raw)


def decode_public_key(text: str) -> Ed25519PublicKey:
    """``ed25519:<base64url>``, or plain standard base64 of the 32 raw bytes (edge_server
    fleet.yaml form). Raises ValueError."""
    if not isinstance(text, str):
        raise ValueError("public key must be a string")
    text = text.strip()
    try:
        if text.startswith(PREFIX):
            raw = b64url_decode(text[len(PREFIX) :])
        else:
            raw = base64.b64decode(text, validate=True)
        if len(raw) != 32:
            raise ValueError("an Ed25519 public key is 32 bytes")
        return Ed25519PublicKey.from_public_bytes(raw)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("not an Ed25519 public key") from exc


def sign(key: Ed25519PrivateKey, data: bytes) -> str:
    return PREFIX + b64url_encode(key.sign(data))


def verify(key: Ed25519PublicKey, data: bytes, signature: str) -> bool:
    if not isinstance(signature, str) or not signature.startswith(PREFIX):
        return False
    try:
        raw = b64url_decode(signature[len(PREFIX) :])
        if len(raw) != 64:
            return False
        key.verify(raw, data)
    except (InvalidSignature, ValueError):
        return False
    return True


class KeyRing:
    """Public keys by component reference. A signer ``svc:pdp@field-station`` is found
    under that exact reference first, then under ``svc:pdp``."""

    def __init__(self, keys: Mapping[str, str | Ed25519PublicKey] | None = None):
        self._keys: dict[str, Ed25519PublicKey] = {}
        for ref, key in (keys or {}).items():
            self.add(ref, key)

    def add(self, ref: str, key: str | Ed25519PublicKey) -> None:
        self._keys[ref] = decode_public_key(key) if isinstance(key, str) else key

    def remove(self, ref: str) -> None:
        self._keys.pop(ref, None)

    def get(self, signer: str) -> Ed25519PublicKey | None:
        if signer in self._keys:
            return self._keys[signer]
        return self._keys.get(signer.split("@", 1)[0])

    def __contains__(self, signer: object) -> bool:
        return isinstance(signer, str) and self.get(signer) is not None

    def refs(self) -> Iterable[str]:
        return self._keys.keys()
