"""Rules JSON Schema cannot express: cross-field, cross-object and signature checks."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Iterable, Mapping

from . import crypto
from .schemas import (
    CONTRACTS, LEVELS, SPEC_VERSION, TYPE_SCHEMAS, schema_errors, schema_for_uri,
)


@dataclass
class Finding:
    level: str  # "error" | "warning"
    where: str  # file and JSON path
    message: str

    def __str__(self) -> str:
        return f"{self.level.upper():7} {self.where}: {self.message}"


@dataclass
class Context:
    keys: Mapping[str, str] = field(default_factory=dict)
    manifest: Mapping[str, Any] | None = None
    # (source, seq) and (source, id) already seen across samples.
    seen_seq: dict = field(default_factory=dict)
    seen_id: dict = field(default_factory=dict)


def _ts(s: Any) -> datetime | None:
    if not isinstance(s, str):
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _before(a: Any, b: Any) -> bool:
    """True when both parse and a is strictly before b."""
    ta, tb = _ts(a), _ts(b)
    return ta is not None and tb is not None and ta < tb


def _parse_version(v: str) -> tuple[int, int, int]:
    core = re.split(r"[-+]", v, maxsplit=1)[0]
    parts = [int(p) for p in core.split(".")] + [0, 0]
    return parts[0], parts[1], parts[2]


def range_admits(rng: str, version: str) -> bool:
    """Autonomy range semantics: ^ keeps MAJOR (also for 0), ~ keeps MAJOR.MINOR, bare = prefix."""
    op = rng[0] if rng[:1] in "^~" else ""
    given = [int(p) for p in rng.lstrip("^~").split(".")]
    v = _parse_version(version)
    low = tuple(given + [0] * (3 - len(given)))
    if op == "^":
        return v[0] == given[0] and v >= low
    if op == "~":
        return v[:2] == tuple((given + [0])[:2]) and v >= low
    return tuple(v[: len(given)]) == tuple(given)


def validate(kind: str, obj: Any, where: str, ctx: Context) -> list[Finding]:
    """Schema validation followed by the semantic rules for that kind."""
    out = [Finding("error", f"{where}{p[1:]}", m) for p, m in schema_errors(kind, obj)]
    if out or not isinstance(obj, dict):
        return out  # semantic rules assume a schema-valid object
    rule = RULES.get(kind)
    if rule:
        out.extend(rule(obj, where, ctx))
    if kind in crypto.SIGNED and ctx.keys:
        reason = crypto.verify(obj, kind, ctx.keys)
        if reason:
            out.append(Finding("error", where, reason))
    return out


# --- per-kind rules -------------------------------------------------------

def _manifest(m: dict, where: str, ctx: Context) -> Iterable[Finding]:
    cid = m["component_id"]
    if cid in m.get("dependencies", []) or any(d.split("@")[0] == cid for d in m.get("dependencies", [])):
        yield Finding("error", f"{where}.dependencies", f"{cid} depends on itself")
    for name, rng in m["contracts"].items():
        if name not in CONTRACTS:
            yield Finding("warning", f"{where}.contracts.{name}", f"unknown contract name (known: {', '.join(CONTRACTS)})")
        elif not range_admits(rng, SPEC_VERSION):
            yield Finding("error", f"{where}.contracts.{name}", f"range {rng} does not admit spec {SPEC_VERSION}")
    caps = m["capabilities"]
    names = [p["name"] for p in caps.get("provides", [])]
    for dup in sorted({n for n in names if names.count(n) > 1}):
        yield Finding("error", f"{where}.capabilities.provides", f"capability {dup} provided twice")
    if caps.get("proposes_actions") and "action-lifecycle" not in m["contracts"]:
        yield Finding("error", f"{where}.contracts", "proposes_actions requires the action-lifecycle contract")
    if "health" in m["contracts"] and "health" not in m["runtime"]:
        yield Finding("warning", f"{where}.runtime", "no HTTP health endpoints: the component must publish health.reported events every 60 s")


def _envelope(e: dict, where: str, ctx: Context) -> Iterable[Finding]:
    src = e["source"]
    if ctx.manifest is not None and src.split("@")[0] != ctx.manifest["component_id"]:
        yield Finding("error", f"{where}.source", f"{src} is not the manifest's component {ctx.manifest['component_id']}")
    key = (src, e["seq"])
    if key in ctx.seen_seq:
        yield Finding("error", f"{where}.seq", f"seq {e['seq']} from {src} already used in {ctx.seen_seq[key]}")
    ctx.seen_seq[key] = where
    key = (src, e["id"])
    if key in ctx.seen_id:
        yield Finding("error", f"{where}.id", f"id {e['id']} from {src} already used in {ctx.seen_id[key]}")
    ctx.seen_id[key] = where
    if "expires" in e and not _before(e["time"], e["expires"]):
        yield Finding("error", f"{where}.expires", "expires is not after time")

    expected = TYPE_SCHEMAS.get(e["type"])
    ds = e.get("dataschema")
    named = schema_for_uri(ds) if ds else None
    if ds and ds.startswith("https://autonomy.local/spec/") and named is None:
        yield Finding("error", f"{where}.dataschema", f"{ds} is not a v0 spec schema URI")
    if expected and ds is None:
        yield Finding("warning", f"{where}.dataschema", f"type {e['type']} should carry dataschema for {expected}")
    if expected and named and named != expected:
        yield Finding("error", f"{where}.dataschema", f"type {e['type']} carries {named}, expected {expected}")
    kind = named or expected
    if kind and "data" not in e:
        yield Finding("error", where, f"type {e['type']} requires data")
    elif kind:
        if kind == "envelope":
            yield Finding("error", f"{where}.dataschema", "an envelope cannot carry an envelope")
            return
        yield from validate(kind, e["data"], f"{where}.data", Context(keys=ctx.keys))
        if kind == "action-proposal" and isinstance(e["data"], dict):
            if any(i.get("trust") == "untrusted" for i in e["data"].get("inputs", [])) and e["trust"] != "untrusted":
                yield Finding("error", f"{where}.trust", "proposal uses untrusted inputs, so the envelope must be trust: untrusted")


def _grant(g: dict, where: str, ctx: Context) -> Iterable[Finding]:
    if not _before(g["valid_from"], g["expires"]):
        yield Finding("error", f"{where}.expires", "expires is not after valid_from")
    if g["grantee"].startswith("owner:"):
        yield Finding("error", f"{where}.grantee", "grants are issued to components, not to the owner")
    vr = g["constraints"].get("value_range")
    if vr and vr["min"] > vr["max"]:
        yield Finding("error", f"{where}.constraints.value_range", "min is greater than max")


def _expires_after(start_field: str) -> Callable[[dict, str, Context], Iterable[Finding]]:
    def rule(o: dict, where: str, ctx: Context) -> Iterable[Finding]:
        if start_field in o and not _before(o[start_field], o["expires"]):
            yield Finding("error", f"{where}.expires", f"expires is not after {start_field}")
    return rule


def _action_request(r: dict, where: str, ctx: Context) -> Iterable[Finding]:
    p, d, a = r["proposal"], r["decision"], r.get("approval")
    if d["proposal_id"] != p["proposal_id"]:
        yield Finding("error", f"{where}.decision.proposal_id", "decision is for a different proposal")
    expected = crypto.params_hash(p["action"])
    if d["params_hash"] != expected:
        yield Finding("error", f"{where}.decision.params_hash", f"does not bind proposal.action (expected {expected})")
    if r["idempotency_key"] != p["idempotency_key"]:
        yield Finding("error", f"{where}.idempotency_key", "differs from proposal.idempotency_key")
    if a is not None:
        if a["decision_id"] != d["decision_id"]:
            yield Finding("error", f"{where}.approval.decision_id", "approval is for a different decision")
        if a["params_hash"] != d["params_hash"]:
            yield Finding("error", f"{where}.approval.params_hash", "differs from decision.params_hash")
    for kind, sub in (("decision", d), ("approval", a)):
        if sub is not None:
            rule = RULES.get(kind)
            if rule:
                yield from rule(sub, f"{where}.{kind}", ctx)
            if ctx.keys:
                reason = crypto.verify(sub, kind, ctx.keys)
                if reason:
                    yield Finding("error", f"{where}.{kind}", reason)


def _execution_record(x: dict, where: str, ctx: Context) -> Iterable[Finding]:
    if "finished_at" in x and "started_at" in x and _before(x["finished_at"], x["started_at"]):
        yield Finding("error", f"{where}.finished_at", "finished_at is before started_at")


def _audit_record(rec: dict, where: str, ctx: Context) -> Iterable[Finding]:
    expected = crypto.audit_hash(rec)
    if rec["hash"] != expected:
        yield Finding("error", f"{where}.hash", f"hash does not match the record (expected {expected})")
    if rec["seq"] == 0 and rec["prev_hash"] != crypto.GENESIS_HASH:
        yield Finding("error", f"{where}.prev_hash", "the first record (seq 0) must have the genesis prev_hash")


def _registry(r: dict, where: str, ctx: Context) -> Iterable[Finding]:
    owners = r["owners"]
    reason = crypto.verify_with_key(owners, "signature", owners["root"])
    if reason:
        yield Finding("error", f"{where}.owners.signature", f"root {reason}")
    seen: dict[bytes, str] = {}

    def claim(text: str, who: str, path: str) -> Iterable[Finding]:
        raw = crypto.key_bytes(text)
        if raw is None:
            yield Finding("error", path, "not an Ed25519 public key")
        elif raw in seen:
            yield Finding("error", path, f"key already used by {seen[raw]}")
        else:
            seen[raw] = who

    yield from claim(owners["root"], "owner:root", f"{where}.owners.root")
    for i, op in enumerate(owners["ops"]):
        yield from claim(op["key"], "owner:ops", f"{where}.owners.ops[{i}].key")
    for name, node in r["nodes"].items():
        yield from claim(node["public_key"], f"cell:{name}", f"{where}.nodes.{name}.public_key")
    for cid, comp in r.get("components", {}).items():
        yield from claim(comp["public_key"], cid, f"{where}.components.{cid}.public_key")
        for cell in comp["cells"]:
            if cell not in r["nodes"]:
                yield Finding("error", f"{where}.components.{cid}.cells", f"{cell} is not a node of this registry")
        for name, rng in comp.get("contracts", {}).items():
            if name in CONTRACTS and not range_admits(rng, SPEC_VERSION):
                yield Finding("error", f"{where}.components.{cid}.contracts.{name}", f"range {rng} does not admit spec {SPEC_VERSION}")
    present = {f"cell:{n}" for n in r["nodes"]} | set(r.get("components", {}))
    for i, rev in enumerate(r.get("revoked", [])):
        if rev["id"].split("@")[0] in present:
            yield Finding("error", f"{where}.revoked[{i}].id", f"{rev['id']} is revoked but still listed")
        raw = crypto.key_bytes(rev["key"])
        if raw is not None and raw in seen:
            yield Finding("error", f"{where}.revoked[{i}].key", f"revoked key is still used by {seen[raw]}")
    for name, node in r["nodes"].items():
        if node["site"] not in r.get("sites", {node["site"]: None}):
            yield Finding("error", f"{where}.nodes.{name}.site", f"site {node['site']} is not in sites")


def _registry_signature(s: dict, where: str, ctx: Context) -> Iterable[Finding]:
    reason = crypto.verify_with_key(s, "signature", s["key"])
    if reason:
        yield Finding("error", f"{where}.signature", reason)


RULES: dict[str, Callable[[dict, str, Context], Iterable[Finding]]] = {
    "manifest": _manifest,
    "envelope": _envelope,
    "grant": _grant,
    "decision": _expires_after("decided_at"),
    "approval": _expires_after("approved_at"),
    "action-request": _action_request,
    "execution-record": _execution_record,
    "audit-record": _audit_record,
    "registry": _registry,
    "registry-signature": _registry_signature,
}


def check_audit_chain(records: list[tuple[str, dict]]) -> list[Finding]:
    """Continuity of consecutive records from one cell (e.g. one .jsonl file)."""
    out: list[Finding] = []
    for (_, prev), (where, rec) in zip(records, records[1:]):
        if rec.get("cell") != prev.get("cell"):
            out.append(Finding("error", where, "chain mixes cells"))
        if rec.get("seq") != prev.get("seq", -2) + 1:
            out.append(Finding("error", f"{where}.seq", f"expected seq {prev.get('seq')} + 1"))
        if rec.get("prev_hash") != prev.get("hash"):
            out.append(Finding("error", f"{where}.prev_hash", "does not equal the previous record's hash"))
    return out


def check_level(manifest: dict, level: str, where: str) -> list[Finding]:
    missing = [c for c in LEVELS[level] if c not in manifest.get("contracts", {})]
    return [Finding("error", f"{where}.contracts", f"level {level} requires contract {c}") for c in missing]
