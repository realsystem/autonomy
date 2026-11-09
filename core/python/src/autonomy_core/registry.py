"""The owner-signed registry (spec registry and registry-signature schemas).

A cell accepts a registry only if:

1. ``owners.root`` is the root key it trusts (pinned; the first accepted registry pins it),
2. root's signature over ``owners`` verifies — only root changes who the owners are,
3. the detached signature is by root or by a delegated, unexpired ops key, over the
   canonical hash of exactly this document,
4. its serial is above the one the cell holds (equal is fine for the same document),
5. the document is consistent: one key per identity, components on listed cells,
   revoked identities and keys absent.

``RegistryStore`` keeps the pin and the last accepted registry on disk, so a cell keeps
working with the last valid version when an update is refused.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization

from ._fs import atomic_write
from .errors import Reject
from .ids import is_name, parse_rfc3339, to_rfc3339
from .jcs import canonicalize
from .keys import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
    KeyRing,
    decode_public_key,
    encode_public_key,
    sign,
    verify,
)
from .signing import sha256_tagged

REGISTRY_FORMAT = 1
SIGNERS = ("owner:root", "owner:ops")


def registry_hash(doc: Mapping[str, Any]) -> str:
    """sha256 of the canonical JSON of the registry document."""
    return sha256_tagged(canonicalize(dict(doc)))


def _raw(key: Ed25519PublicKey) -> bytes:
    return key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def _key(text: Any, what: str) -> Ed25519PublicKey:
    try:
        return decode_public_key(str(text))
    except ValueError:
        raise Reject("invalid registry", f"{what}: not an Ed25519 public key") from None


def same_key(a: str, b: str) -> bool:
    """True when two key texts (either encoding) are the same key."""
    try:
        return _raw(decode_public_key(a)) == _raw(decode_public_key(b))
    except ValueError:
        return False


def sign_owners(owners: dict[str, Any], root_key: Ed25519PrivateKey) -> dict[str, Any]:
    """Set owners.root to root_key's public key and sign the section with it."""
    owners = {k: v for k, v in owners.items() if k != "signature"}
    owners["root"] = encode_public_key(root_key)
    owners.setdefault("ops", [])
    owners["signature"] = sign(root_key, canonicalize(owners))
    return owners


def sign_registry(
    doc: Mapping[str, Any],
    key: Ed25519PrivateKey,
    *,
    serial: int,
    signer: str = "owner:ops",
    now: float | None = None,
    comment: str | None = None,
) -> dict[str, Any]:
    """The detached signature document for ``doc``."""
    if signer not in SIGNERS:
        raise ValueError(f"signer must be one of {SIGNERS}")
    sig: dict[str, Any] = {
        "registry_hash": registry_hash(doc),
        "serial": serial,
        "signed_at": to_rfc3339(time.time() if now is None else float(int(now))),
        "signer": signer,
        "key": encode_public_key(key),
    }
    if comment:
        sig["comment"] = comment
    sig["signature"] = sign(key, canonicalize(sig))
    return sig


def check_document(doc: Any) -> list[str]:
    """Consistency problems of a registry document (empty: consistent). Signatures are
    checked by verify_registry."""
    problems: list[str] = []
    if not isinstance(doc, dict) or doc.get("registry_format") != REGISTRY_FORMAT:
        return [f"not a registry (registry_format {REGISTRY_FORMAT} expected)"]
    owners, nodes = doc.get("owners"), doc.get("nodes")
    components, revoked = doc.get("components", {}), doc.get("revoked", [])
    if not isinstance(owners, dict) or not isinstance(owners.get("ops"), list):
        return ["owners must have root, ops and signature"]
    if not isinstance(nodes, dict) or not isinstance(components, dict) or not isinstance(revoked, list):
        return ["nodes and components must be objects, revoked a list"]
    seen: dict[bytes, str] = {}

    def claim(text: Any, who: str) -> None:
        try:
            raw = _raw(decode_public_key(str(text)))
        except ValueError:
            problems.append(f"{who}: not an Ed25519 public key")
            return
        if raw in seen:
            problems.append(f"{who}: key already used by {seen[raw]}")
        else:
            seen[raw] = who

    claim(owners.get("root"), "owner:root")
    for op in owners["ops"]:
        if not isinstance(op, dict):
            problems.append("owners.ops entries must be objects")
            continue
        claim(op.get("key"), "owner:ops")
        try:
            parse_rfc3339(op.get("expires"))  # type: ignore[arg-type]
        except ValueError:
            problems.append("owner:ops: expires must be an RFC 3339 UTC time")
    sites = doc.get("sites")
    for name, node in nodes.items():
        if not is_name(name):
            problems.append(f"node {name}: names use lowercase letters, digits and single hyphens")
        if not isinstance(node, dict):
            problems.append(f"node {name}: must be an object")
            continue
        claim(node.get("public_key"), f"cell:{name}")
        if isinstance(sites, dict) and node.get("site") not in sites:
            problems.append(f"node {name}: site {node.get('site')} is not in sites")
    for cid, comp in components.items():
        if not isinstance(comp, dict):
            problems.append(f"{cid}: must be an object")
            continue
        claim(comp.get("public_key"), cid)
        cells = comp.get("cells")
        if not isinstance(cells, list) or not cells:
            problems.append(f"{cid}: cells must list at least one node")
            continue
        for cell in cells:
            if cell not in nodes:
                problems.append(f"{cid}: {cell} is not a node of this registry")
    present = {f"cell:{n}" for n in nodes} | set(components)
    for rev in revoked:
        if not isinstance(rev, dict):
            problems.append("revoked entries must be objects")
            continue
        if str(rev.get("id", "")).split("@")[0] in present:
            problems.append(f"{rev.get('id')} is revoked but still listed")
        try:
            raw = _raw(decode_public_key(str(rev.get("key"))))
        except ValueError:
            problems.append(f"revoked {rev.get('id')}: not an Ed25519 public key")
            continue
        if raw in seen:
            problems.append(f"revoked key of {rev.get('id')} is still used by {seen[raw]}")
    return problems


@dataclass(frozen=True)
class Component:
    id: str
    key: Ed25519PublicKey
    version: str
    cells: tuple[str, ...]
    manifest_hash: str


@dataclass(frozen=True)
class Registry:
    """A verified registry."""

    doc: dict[str, Any]
    signature: dict[str, Any]
    hash: str
    serial: int
    root: str
    signer: str
    cell_keys: dict[str, Ed25519PublicKey] = field(repr=False)
    components: dict[str, Component] = field(repr=False)
    revoked_keys: frozenset[bytes] = field(repr=False)

    def keyring(self, cell: str | None = None) -> KeyRing:
        """Keys of every cell (as cell:<name>) and of the components; with ``cell``, only
        the components that list it."""
        ring = KeyRing()
        for name, key in self.cell_keys.items():
            ring.add(f"cell:{name}", key)
        for comp in self.components.values():
            if cell is None or cell in comp.cells:
                ring.add(comp.id, comp.key)
        return ring

    def is_revoked(self, key: Ed25519PublicKey) -> bool:
        return _raw(key) in self.revoked_keys

    def components_for(self, cell: str) -> list[Component]:
        return [c for c in self.components.values() if cell in c.cells]


def verify_registry(
    doc: Mapping[str, Any],
    signature: Mapping[str, Any],
    *,
    root: str | None,
    previous: Mapping[str, Any] | None = None,
    now: float | None = None,
) -> Registry:
    """Verify a registry and its detached signature; raise Reject otherwise.

    ``root``: the pinned root key (None accepts the document's root — first use only).
    ``previous``: the signature document of the registry currently held.
    """
    t = time.time() if now is None else now
    if not isinstance(doc, dict) or not isinstance(signature, dict):
        raise Reject("malformed", "registry and signature must be objects")
    for k in ("registry_hash", "serial", "signed_at", "signer", "key", "signature"):
        if k not in signature:
            raise Reject("malformed", f"signature has no {k}")
    serial = signature["serial"]
    if not isinstance(serial, int) or isinstance(serial, bool) or serial < 1:
        raise Reject("malformed", "serial")
    problems = check_document(doc)
    if problems:
        raise Reject("invalid registry", "; ".join(problems))
    digest = registry_hash(doc)
    if signature["registry_hash"] != digest:
        raise Reject("registry hash mismatch", "the signature is for another document")

    owners = doc["owners"]
    root_key = _key(owners.get("root"), "owners.root")
    if root is not None and not same_key(root, owners["root"]):
        raise Reject("untrusted root", "owners.root is not the pinned root key")
    owners_body = {k: v for k, v in owners.items() if k != "signature"}
    if not verify(root_key, canonicalize(owners_body), str(owners.get("signature", ""))):
        raise Reject("bad owners signature", "owners must be signed by root")

    try:
        signed_at = parse_rfc3339(signature["signed_at"])
    except ValueError:
        raise Reject("malformed", "signed_at") from None
    if signed_at > t + 300:
        raise Reject("from the future", "signed_at")
    signer = signature["signer"]
    if signer == "owner:root":
        if not same_key(str(signature["key"]), owners["root"]):
            raise Reject("unknown signing key", "owner:root signature not made with owners.root")
    elif signer == "owner:ops":
        match = [op for op in owners["ops"] if same_key(str(signature["key"]), str(op.get("key")))]
        if not match:
            raise Reject("unknown signing key", "not a delegated ops key")
        if parse_rfc3339(match[0]["expires"]) <= signed_at:
            raise Reject("ops key expired", f"expired {match[0]['expires']}")
    else:
        raise Reject("malformed", "signer")
    signing_key = _key(signature["key"], "signature key")
    body = {k: v for k, v in signature.items() if k != "signature"}
    if not verify(signing_key, canonicalize(body), str(signature["signature"])):
        raise Reject("bad signature", signer)

    if previous is not None:
        prev_serial = previous.get("serial")
        if isinstance(prev_serial, int):
            if serial < prev_serial:
                raise Reject("rollback", f"serial {serial} is older than {prev_serial}")
            if serial == prev_serial and previous.get("registry_hash") != digest:
                raise Reject("serial conflict", f"another registry already has serial {serial}")

    cell_keys = {name: _key(n["public_key"], f"cell:{name}") for name, n in doc["nodes"].items()}
    components = {
        cid: Component(
            id=cid,
            key=_key(c["public_key"], cid),
            version=str(c.get("version", "")),
            cells=tuple(c["cells"]),
            manifest_hash=str(c.get("manifest_hash", "")),
        )
        for cid, c in doc.get("components", {}).items()
    }
    revoked = frozenset(_raw(_key(r["key"], "revoked")) for r in doc.get("revoked", []))
    return Registry(
        doc=dict(doc),
        signature=dict(signature),
        hash=digest,
        serial=serial,
        root=encode_public_key(root_key),
        signer=signer,
        cell_keys=cell_keys,
        components=components,
        revoked_keys=revoked,
    )


class RegistryStore:
    """A cell's pinned root key and last accepted registry, in ``state_dir``."""

    def __init__(self, state_dir: Path | str):
        self.dir = Path(state_dir)
        self.pin_path = self.dir / "owner-root.pub"
        self.doc_path = self.dir / "registry.json"
        self.sig_path = self.dir / "registry.sig.json"

    def pinned_root(self) -> str | None:
        try:
            text = self.pin_path.read_text().strip()
        except OSError:
            return None
        return text or None

    def current(self, root: str | None = None, now: float | None = None) -> Registry | None:
        """The last accepted registry, re-verified, or None."""
        try:
            doc = json.loads(self.doc_path.read_text())
            sig = json.loads(self.sig_path.read_text())
        except (OSError, ValueError):
            return None
        try:
            return verify_registry(doc, sig, root=root or self.pinned_root(), now=now)
        except Reject:
            return None

    def accept(
        self,
        doc: Mapping[str, Any],
        signature: Mapping[str, Any],
        *,
        root: str | None = None,
        now: float | None = None,
    ) -> Registry:
        """Verify an offered registry against the pin and the current one; on success it
        becomes current (and pins the root on first use). Raises Reject."""
        pin = root or self.pinned_root()
        held = self.current(pin, now)
        reg = verify_registry(doc, signature, root=pin, previous=held.signature if held else None, now=now)
        self.dir.mkdir(parents=True, exist_ok=True)
        if pin is None:
            atomic_write(self.pin_path, reg.root + "\n")
        if held is None or held.hash != reg.hash or held.serial != reg.serial:
            atomic_write(self.doc_path, json.dumps(reg.doc, ensure_ascii=False))
            atomic_write(self.sig_path, json.dumps(reg.signature, ensure_ascii=False))
        return reg


def fingerprint(key_text: str) -> str:
    """Short, readable fingerprint for out-of-band comparison: 8 groups of 4 hex digits."""
    digest = sha256_tagged(_raw(decode_public_key(key_text)))[7:39]
    return " ".join(digest[i : i + 4] for i in range(0, 32, 4))


def ops_keys(doc: Mapping[str, Any], now: float | None = None) -> Iterable[Ed25519PublicKey]:
    """Delegated ops keys still valid at ``now`` (for checking owner:ops approvals)."""
    t = time.time() if now is None else now
    for op in doc.get("owners", {}).get("ops", []):
        try:
            if parse_rfc3339(op["expires"]) > t:
                yield decode_public_key(op["key"])
        except (KeyError, ValueError):
            continue
