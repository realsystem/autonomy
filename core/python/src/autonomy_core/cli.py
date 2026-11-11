"""autonomy: owner tools for Autonomy identities and the signed registry.

    autonomy keygen owner-root.pem
    autonomy registry init registry.yaml --root-key owner-root.pem --ops-key "$(cat ops.pub)"
    autonomy registry migrate fleet.yaml registry.yaml --root-key owner-root.pem --ops-key ...
    autonomy registry add-component registry.yaml --manifest autonomy.manifest.json \
        --key ed25519:... --cells home-hub
    autonomy registry sign registry.yaml --key owner-ops.pem      # writes registry.json + registry.sig.json
    autonomy registry verify registry.json --root ed25519:...

Edits rewrite the YAML file and do not keep its comments. Signing never edits it: the
signature covers the canonical JSON, written next to it as <name>.json, which is what
cells are given.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
import time
from pathlib import Path
from typing import Any

from . import __version__
from .errors import Reject
from .ids import is_component_ref, is_name, parse_rfc3339, to_rfc3339
from .jcs import canonicalize
from .keys import (
    decode_public_key,
    encode_public_key,
    generate_private_key,
    load_private_key,
    save_private_key,
)
from .registry import (
    REGISTRY_FORMAT,
    check_document,
    fingerprint,
    registry_hash,
    same_key,
    sign_owners,
    sign_registry,
    verify_registry,
)
from .signing import sha256_tagged


class UsageError(Exception):
    pass


# --- documents ------------------------------------------------------------------


def _yaml() -> Any:
    try:
        import yaml  # type: ignore[import-untyped, unused-ignore]
    except ImportError:
        raise UsageError("YAML files need PyYAML: pip install 'autonomy-core[cli]'") from None
    return yaml


def _loader() -> Any:
    """A safe YAML loader that leaves timestamps as strings, so the document hashes the
    same whether it is read from YAML or JSON."""
    yaml = _yaml()

    class Loader(yaml.SafeLoader):  # type: ignore[name-defined,misc]
        pass

    Loader.yaml_implicit_resolvers = {
        ch: [(tag, rx) for tag, rx in resolvers if tag != "tag:yaml.org,2002:timestamp"]
        for ch, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
    }
    return Loader


def _json_only(value: Any, where: str = "$") -> None:
    if value is None or isinstance(value, (bool, int, float, str)):
        return
    if isinstance(value, list):
        for i, v in enumerate(value):
            _json_only(v, f"{where}[{i}]")
        return
    if isinstance(value, dict):
        for k, v in value.items():
            if not isinstance(k, str):
                raise UsageError(f"{where}: key {k!r} is not a string; quote it")
            _json_only(v, f"{where}.{k}")
        return
    raise UsageError(f"{where}: {type(value).__name__} is not JSON; quote the value")


def load_doc(path: Path) -> Any:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise UsageError(f"cannot read {path}: {exc}") from None
    try:
        if path.suffix in (".yaml", ".yml"):
            doc = _yaml().load(text, Loader=_loader())  # noqa: S506 - SafeLoader subclass
        else:
            doc = json.loads(text)
    except ValueError as exc:
        raise UsageError(f"{path}: {exc}") from None
    except Exception as exc:  # yaml.YAMLError
        raise UsageError(f"{path}: {exc}") from None
    _json_only(doc)
    return doc


def save_doc(path: Path, doc: Any) -> None:
    if path.suffix in (".yaml", ".yml"):
        text = _yaml().safe_dump(doc, sort_keys=False, allow_unicode=True, default_flow_style=False)
    else:
        text = json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def json_path(path: Path) -> Path:
    return path.with_suffix(".json")


def sig_path(path: Path) -> Path:
    return path.with_name(path.with_suffix("").name + ".sig.json")


def _key_text(value: str) -> str:
    """A public key given inline or as a file holding it."""
    p = Path(value)
    text = p.read_text().strip() if p.is_file() else value.strip()
    try:
        return encode_public_key(decode_public_key(text))
    except ValueError:
        raise UsageError(f"{value}: not an Ed25519 public key") from None


def _private(path: str) -> Any:
    try:
        return load_private_key(path)
    except (OSError, ValueError) as exc:
        raise UsageError(f"cannot load private key {path}: {exc}") from None


def _expires(days: float) -> str:
    return to_rfc3339(float(int(time.time() + days * 86400)))


def _registry(path: Path) -> dict[str, Any]:
    doc = load_doc(path)
    if not isinstance(doc, dict) or doc.get("registry_format") != REGISTRY_FORMAT:
        raise UsageError(f"{path} is not a registry (registry_format: {REGISTRY_FORMAT})")
    return doc


def _report_problems(doc: dict[str, Any]) -> None:
    problems = check_document(doc)
    if problems:
        raise UsageError("the registry is inconsistent:\n  " + "\n  ".join(problems))


def _edited(path: Path, doc: dict[str, Any], what: str) -> int:
    _report_problems(doc)
    save_doc(path, doc)
    print(f"{what}; sign it to publish: autonomy registry sign {path} --key <ops key>")
    return 0


# --- commands ------------------------------------------------------------------


def cmd_keygen(args: argparse.Namespace) -> int:
    out = Path(args.out)
    if out.exists():
        raise UsageError(f"{out} exists; keys are never overwritten")
    key = generate_private_key()
    save_private_key(key, out)
    pub = encode_public_key(key)
    out.with_suffix(".pub").write_text(pub + "\n")
    print(f"private key: {out} (keep it secret)")
    print(f"public key:  {pub}")
    print(f"fingerprint: {fingerprint(pub)}")
    return 0


def _owners(root_key_path: str, ops: list[str], days: float) -> dict[str, Any]:
    root = _private(root_key_path)
    entries = [{"key": _key_text(k), "expires": _expires(days)} for k in ops]
    return sign_owners({"ops": entries}, root)


def cmd_init(args: argparse.Namespace) -> int:
    out = Path(args.registry)
    if out.exists():
        raise UsageError(f"{out} exists")
    owners = _owners(args.root_key, args.ops_key, args.ops_days)
    doc: dict[str, Any] = {
        "registry_format": REGISTRY_FORMAT,
        "owners": owners,
        "nodes": {},
        "components": {},
        "revoked": [],
    }
    save_doc(out, doc)
    print(f"wrote {out}; root fingerprint {fingerprint(owners['root'])}")
    return 0


def cmd_migrate(args: argparse.Namespace) -> int:
    fleet = load_doc(Path(args.fleet))
    if not isinstance(fleet, dict) or "nodes" not in fleet:
        raise UsageError(f"{args.fleet} is not an edge_server fleet file")
    out = Path(args.registry)
    if out.exists():
        raise UsageError(f"{out} exists")
    missing = [n for n, node in fleet["nodes"].items() if not (node or {}).get("public_key")]
    if missing:
        raise UsageError(
            "nodes without public_key: "
            + ", ".join(missing)
            + " (deploy each once to create its key, then copy node.pub into the fleet file)"
        )
    bad = [n for n in fleet["nodes"] if not is_name(n)]
    if bad:
        raise UsageError("node names must use single hyphens: " + ", ".join(bad))
    doc: dict[str, Any] = {"registry_format": REGISTRY_FORMAT}
    doc.update({k: copy.deepcopy(v) for k, v in fleet.items()})
    doc["owners"] = _owners(args.root_key, args.ops_key, args.ops_days)
    doc.setdefault("components", {})
    doc.setdefault("revoked", [])
    _report_problems(doc)
    save_doc(out, doc)
    print(
        f"wrote {out} with {len(doc['nodes'])} node(s); root fingerprint {fingerprint(doc['owners']['root'])}"
    )
    return 0


def cmd_add_node(args: argparse.Namespace) -> int:
    path = Path(args.registry)
    doc = _registry(path)
    if not is_name(args.name):
        raise UsageError("node names use lowercase letters, digits and single hyphens")
    if args.name in doc["nodes"] and not args.replace:
        raise UsageError(f"node {args.name} exists (use --replace)")
    node = dict(doc["nodes"].get(args.name, {}))
    node.update({"site": args.site, "public_key": _key_text(args.key)})
    if args.packaging:
        node["packaging"] = args.packaging
    if args.roles:
        node["roles"] = [r.strip() for r in args.roles.split(",") if r.strip()]
    doc["nodes"][args.name] = node
    if isinstance(doc.get("sites"), dict):
        doc["sites"].setdefault(args.site, None)
    return _edited(path, doc, f"node {args.name} added")


def cmd_add_component(args: argparse.Namespace) -> int:
    path = Path(args.registry)
    doc = _registry(path)
    manifest = load_doc(Path(args.manifest))
    if not isinstance(manifest, dict) or "component_id" not in manifest or "version" not in manifest:
        raise UsageError(f"{args.manifest} is not a component manifest")
    cid = manifest["component_id"]
    if not is_component_ref(cid) or "@" in cid or cid.startswith("owner:"):
        raise UsageError(f"{cid} is not a component_id")
    comps = doc.setdefault("components", {})
    if cid in comps and not args.replace:
        raise UsageError(f"{cid} exists (use --replace for a new version or key)")
    entry: dict[str, Any] = {
        "public_key": _key_text(args.key),
        "version": manifest["version"],
        "manifest_hash": sha256_tagged(canonicalize(manifest)),
        "cells": [c.strip() for c in args.cells.split(",") if c.strip()],
    }
    if isinstance(manifest.get("contracts"), dict):
        entry["contracts"] = manifest["contracts"]
    if args.comment:
        entry["comment"] = args.comment
    comps[cid] = entry
    return _edited(path, doc, f"{cid} {manifest['version']} added")


def cmd_revoke(args: argparse.Namespace) -> int:
    path = Path(args.registry)
    doc = _registry(path)
    ident = args.id
    if ident.startswith("cell:"):
        entry = doc["nodes"].pop(ident[5:], None)
    else:
        entry = doc.get("components", {}).pop(ident, None)
    if entry is None:
        raise UsageError(f"{ident} is not in the registry")
    doc.setdefault("revoked", []).append(
        {
            "id": ident,
            "key": entry["public_key"],
            "revoked_at": to_rfc3339(float(int(time.time()))),
            "reason": args.reason,
        }
    )
    for comp in doc.get("components", {}).values():
        if ident.startswith("cell:") and ident[5:] in comp.get("cells", []):
            comp["cells"].remove(ident[5:])
    return _edited(path, doc, f"{ident} revoked")


def cmd_delegate(args: argparse.Namespace) -> int:
    path = Path(args.registry)
    doc = _registry(path)
    ops = [
        op for op in doc["owners"]["ops"] if not any(same_key(op["key"], _key_text(k)) for k in args.remove)
    ]
    for k in args.add:
        ops.append({"key": _key_text(k), "expires": _expires(args.days)})
    root = _private(args.root_key)
    if not same_key(encode_public_key(root), doc["owners"]["root"]) and not args.new_root:
        raise UsageError("that is not this registry's root key (use --new-root to rotate the root)")
    doc["owners"] = sign_owners({"ops": ops}, root)
    return _edited(path, doc, f"owners re-signed by root ({len(ops)} ops key(s))")


def cmd_sign(args: argparse.Namespace) -> int:
    path = Path(args.registry)
    doc = _registry(path)
    _report_problems(doc)
    out_doc = json_path(path)
    out_sig = sig_path(out_doc)
    previous = (
        load_doc(Path(args.previous)) if args.previous else (load_doc(out_sig) if out_sig.exists() else None)
    )
    serial = args.serial or ((previous or {}).get("serial", 0) + 1)
    key = _private(args.key)
    signer = args.signer
    if signer is None:
        signer = "owner:root" if same_key(encode_public_key(key), doc["owners"]["root"]) else "owner:ops"
    sig = sign_registry(doc, key, serial=serial, signer=signer, comment=args.comment)
    try:
        verify_registry(doc, sig, root=doc["owners"]["root"], previous=previous)
    except Reject as why:
        raise UsageError(f"refusing to write a signature cells would reject: {why}") from None
    if out_doc != path:
        save_doc(out_doc, doc)
    save_doc(out_sig, sig)
    print(f"signed serial {serial} as {signer}: {out_doc} + {out_sig}")
    print(f"registry hash {sig['registry_hash']}")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    path = Path(args.registry)
    doc = load_doc(path)
    sig = load_doc(Path(args.signature) if args.signature else sig_path(json_path(path)))
    previous = load_doc(Path(args.previous)) if args.previous else None
    root = _key_text(args.root) if args.root else None
    try:
        reg = verify_registry(doc, sig, root=root, previous=previous)
    except Reject as why:
        print(f"REJECTED: {why}")
        return 1
    print(f"OK: serial {reg.serial}, signed by {reg.signer} at {sig['signed_at']}")
    print(
        f"root fingerprint {fingerprint(reg.root)}"
        + ("" if root else " (not pinned: pass --root to check it)")
    )
    print(
        f"{len(reg.cell_keys)} node(s), {len(reg.components)} component(s), {len(reg.revoked_keys)} revoked"
    )
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    doc = _registry(Path(args.registry))
    owners = doc["owners"]
    print(f"root   {fingerprint(owners['root'])}")
    for op in owners["ops"]:
        state = "expired" if parse_rfc3339(op["expires"]) <= time.time() else f"until {op['expires']}"
        print(f"ops    {fingerprint(op['key'])}  {state}")
    for name, node in doc["nodes"].items():
        print(f"cell:{name:<20} site {node.get('site')}  roles {','.join(node.get('roles', []))}")
    for cid, comp in doc.get("components", {}).items():
        print(f"{cid:<25} {comp['version']:<10} on {','.join(comp['cells'])}")
    for rev in doc.get("revoked", []):
        print(f"revoked {rev['id']} at {rev['revoked_at']}: {rev['reason']}")
    print(f"hash {registry_hash(doc)}")
    return 0


def cmd_fingerprint(args: argparse.Namespace) -> int:
    print(fingerprint(_key_text(args.key)))
    return 0


def cmd_scan(args: argparse.Namespace) -> int:
    from .secrets import scan

    allow = Path(args.allowlist) if args.allowlist else None
    if allow is None and Path(".secrets-allowlist").exists():
        allow = Path(".secrets-allowlist")
    findings = scan(args.paths or ["."], allowlist=allow)
    for f in findings:
        print(f)
    print(f"{'FAIL' if findings else 'PASS'}: {len(findings)} possible secret(s)")
    return 1 if findings else 0


def cmd_check_sops(args: argparse.Namespace) -> int:
    from .secrets import check_sops

    bad = 0
    for path in args.files:
        problems = check_sops(load_doc(Path(path)))
        for p in problems:
            print(f"{path}: {p}")
        bad += bool(problems)
    print(f"{'FAIL' if bad else 'PASS'}: {bad} of {len(args.files)} file(s) not properly encrypted")
    return 1 if bad else 0


def cmd_trace(args: argparse.Namespace) -> int:
    from .telemetry import read_spans, reconstruct

    audit = [json.loads(line) for line in Path(args.audit).read_text().splitlines() if line.strip()]
    story = reconstruct(args.correlationid, audit, read_spans(args.traces) if args.traces else [])
    if not story["steps"]:
        print(f"no audit records for {args.correlationid}")
        return 1
    print("\n".join(story["steps"]))
    print(f"refs: {json.dumps(story['refs'])}")
    print(f"trace(s): {', '.join(story['trace_ids']) or '-'}; spans: {', '.join(story['spans']) or '-'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="autonomy", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("keygen", help="create an Ed25519 key pair (PEM + .pub)")
    p.add_argument("out")
    p.set_defaults(func=cmd_keygen)
    p = sub.add_parser("fingerprint", help="fingerprint of a public key, for out-of-band checks")
    p.add_argument("key")
    p.set_defaults(func=cmd_fingerprint)

    sec = sub.add_parser("secrets", help="secret scanning (IT-81)").add_subparsers(dest="sub", required=True)
    p = sec.add_parser("scan", help="find secret-shaped text in files; exit 1 if any")
    p.add_argument("paths", nargs="*")
    p.add_argument(
        "--allowlist", help="'<glob> [rule]' lines of expected test data (default .secrets-allowlist)"
    )
    p.set_defaults(func=cmd_scan)
    p = sec.add_parser("check-sops", help="check that files meant to be SOPS/age encrypted are (ADR-013)")
    p.add_argument("files", nargs="+")
    p.set_defaults(func=cmd_check_sops)
    p = sub.add_parser("trace", help="reconstruct an action story from the audit log and traces (IT-71)")
    p.add_argument("correlationid")
    p.add_argument("--audit", required=True, help="audit JSON Lines file")
    p.add_argument("--traces", help="OTLP/JSON lines file")
    p.set_defaults(func=cmd_trace)

    reg = sub.add_parser("registry", help="the owner-signed registry").add_subparsers(
        dest="sub", required=True
    )

    def owner_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--root-key", required=True, help="owner:root private key (PEM)")
        p.add_argument(
            "--ops-key", action="append", default=[], help="owner:ops public key to delegate (repeatable)"
        )
        p.add_argument("--ops-days", type=float, default=365, help="ops key validity in days (default 365)")

    p = reg.add_parser("init", help="start an empty registry")
    p.add_argument("registry")
    owner_args(p)
    p.set_defaults(func=cmd_init)
    p = reg.add_parser("migrate", help="turn an edge_server fleet.yaml into a registry")
    p.add_argument("fleet")
    p.add_argument("registry")
    owner_args(p)
    p.set_defaults(func=cmd_migrate)
    p = reg.add_parser("add-node", help="add or replace a cell")
    p.add_argument("registry")
    p.add_argument("name")
    p.add_argument("--site", required=True)
    p.add_argument("--key", required=True, help="public key (or a file holding it, e.g. node.pub)")
    p.add_argument("--roles", default="")
    p.add_argument("--packaging", default="")
    p.add_argument("--replace", action="store_true")
    p.set_defaults(func=cmd_add_node)
    p = reg.add_parser("add-component", help="enrol a component from its manifest and public key")
    p.add_argument("registry")
    p.add_argument("--manifest", required=True)
    p.add_argument("--key", required=True)
    p.add_argument("--cells", required=True, help="comma-separated cells it runs on or reports to")
    p.add_argument("--comment", default="")
    p.add_argument("--replace", action="store_true")
    p.set_defaults(func=cmd_add_component)
    p = reg.add_parser("revoke", help="remove an identity and keep its key on the revoked list")
    p.add_argument("registry")
    p.add_argument("id", help="cell:<name> or a component_id")
    p.add_argument("--reason", required=True)
    p.set_defaults(func=cmd_revoke)
    p = reg.add_parser("delegate", help="change the ops keys (needs the root key)")
    p.add_argument("registry")
    p.add_argument("--root-key", required=True)
    p.add_argument("--add", action="append", default=[])
    p.add_argument("--remove", action="append", default=[])
    p.add_argument("--days", type=float, default=365)
    p.add_argument("--new-root", action="store_true", help="the root key is a new one (root rotation)")
    p.set_defaults(func=cmd_delegate)
    p = reg.add_parser("sign", help="sign: writes <name>.json and <name>.sig.json")
    p.add_argument("registry")
    p.add_argument("--key", required=True, help="owner:ops (or owner:root) private key")
    p.add_argument("--signer", choices=["owner:root", "owner:ops"])
    p.add_argument("--serial", type=int, default=0, help="default: previous serial + 1")
    p.add_argument(
        "--previous", help="signature of the registry the cells hold (default: the existing .sig.json)"
    )
    p.add_argument("--comment", default="")
    p.set_defaults(func=cmd_sign)
    p = reg.add_parser("verify", help="verify a registry and its signature")
    p.add_argument("registry")
    p.add_argument("--signature")
    p.add_argument("--root", help="the pinned root key; without it the document's own root is used")
    p.add_argument("--previous", help="signature the cell holds, to check the serial")
    p.set_defaults(func=cmd_verify)
    p = reg.add_parser("show", help="summary")
    p.add_argument("registry")
    p.set_defaults(func=cmd_show)

    args = ap.parse_args(argv)
    try:
        return int(args.func(args))
    except UsageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
