"""autonomy-conformance: validate Autonomy contracts in any repository's CI."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterator

from . import __version__, checks, compat, crypto
from .jcs import canonicalize
from .schemas import LEVELS, SPEC_VERSION, schema_dir, schema_names


def _load_keys(path: str | None) -> dict[str, str]:
    if not path:
        return {}
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    return dict(doc.get("public", doc))


def _iter_docs(path: Path) -> Iterator[tuple[str, Any]]:
    """Yield (location, document) for a .json file or each line of a .jsonl file."""
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".jsonl":
        for n, line in enumerate(text.splitlines(), 1):
            if line.strip():
                yield f"{path}:{n}", json.loads(line)
    else:
        yield str(path), json.loads(text)


def _kind_for(path: Path, doc: Any, names: set[str]) -> str | None:
    """Sample kind: parent directory name, then <name>.<kind>.json(l), then envelope detection."""
    if path.parent.name in names:
        return path.parent.name
    stem = path.name.rsplit(".", 1)[0]
    if "." in stem and stem.rsplit(".", 1)[1] in names:
        return stem.rsplit(".", 1)[1]
    if isinstance(doc, dict) and "specversion" in doc:
        return "envelope"
    return None


def _report(findings: list[checks.Finding], strict: bool, quiet: bool = False) -> int:
    errors = [f for f in findings if f.level == "error"]
    warnings = [f for f in findings if f.level == "warning"]
    for f in findings:
        print(f)
    failed = bool(errors) or (strict and bool(warnings))
    if not quiet:
        print(f"{'FAIL' if failed else 'PASS'}: {len(errors)} error(s), {len(warnings)} warning(s)")
    return 1 if failed else 0


def check_files(files: list[Path], ctx: checks.Context, forced_kind: str | None = None) -> tuple[list[checks.Finding], int]:
    names = set(schema_names())
    findings: list[checks.Finding] = []
    count = 0
    for path in files:
        try:
            docs = list(_iter_docs(path))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            findings.append(checks.Finding("error", str(path), f"not valid JSON: {e}"))
            continue
        chain: list[tuple[str, dict]] = []
        for where, doc in docs:
            kind = forced_kind or _kind_for(path, doc, names)
            if kind is None:
                findings.append(checks.Finding("error", where, "cannot tell which schema applies; put it in a directory named after the schema or name it <x>.<schema>.json"))
                continue
            count += 1
            f = checks.validate(kind, doc, where, ctx)
            findings.extend(f)
            if kind == "audit-record" and not f:
                chain.append((where, doc))
        findings.extend(checks.check_audit_chain(chain))
    return findings, count


def cmd_validate(args: argparse.Namespace) -> int:
    if args.schema not in schema_names():
        print(f"unknown schema {args.schema!r}; known: {', '.join(schema_names())}", file=sys.stderr)
        return 2
    ctx = checks.Context(keys=_load_keys(args.keys))
    findings, _ = check_files([Path(f) for f in args.files], ctx, forced_kind=args.schema)
    return _report(findings, args.strict)


def cmd_check(args: argparse.Namespace) -> int:
    keys = _load_keys(args.keys)
    mpath = Path(args.manifest)
    try:
        manifest = json.loads(mpath.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"cannot read manifest: {e}", file=sys.stderr)
        return 2
    findings = checks.validate("manifest", manifest, str(mpath), checks.Context(keys=keys))
    if args.level:
        findings += checks.check_level(manifest, args.level, str(mpath))
    count = 0
    if args.samples:
        root = Path(args.samples)
        files = sorted(p for p in root.rglob("*") if p.suffix in (".json", ".jsonl") and p.is_file())
        if not files:
            findings.append(checks.Finding("warning", str(root), "no samples found"))
        ctx = checks.Context(keys=keys, manifest=manifest if not findings else None)
        f, count = check_files(files, ctx)
        findings += f
    print(f"autonomy-conformance {__version__} · spec {SPEC_VERSION} · {manifest.get('component_id', '?')} · level {args.level or '-'} · {count} sample(s)")
    return _report(findings, args.strict)


def cmd_selftest(args: argparse.Namespace) -> int:
    """Every valid fixture passes, every invalid fixture fails for the stated reason."""
    root = Path(args.fixtures) if args.fixtures else Path(__file__).resolve().parent.parent / "fixtures" / "v0"
    if not root.is_dir():
        print(f"fixtures not found at {root}", file=sys.stderr)
        return 2
    ctx_keys = _load_keys(str(root.parent / "keys" / "test-keys.json"))
    problems: list[str] = []
    n = 0
    for kind_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        kind = kind_dir.name
        for path in sorted((kind_dir / "valid").glob("*.json*")):
            for where, doc in _iter_docs(path):
                n += 1
                f = [x for x in checks.validate(kind, doc, where, checks.Context(keys=ctx_keys)) if x.level == "error"]
                if f:
                    problems.append(f"valid fixture rejected: {where}\n    " + "\n    ".join(map(str, f)))
            if path.suffix == ".jsonl" and kind == "audit-record":
                for f in checks.check_audit_chain(list(_iter_docs(path))):
                    problems.append(f"valid chain broken: {f}")
        for path in sorted((kind_dir / "invalid").glob("*.json")):
            n += 1
            case = json.loads(path.read_text(encoding="utf-8"))
            f = [x for x in checks.validate(kind, case["instance"], str(path), checks.Context(keys=ctx_keys)) if x.level == "error"]
            if not f:
                problems.append(f"invalid fixture accepted: {path} ({case['description']})")
            elif not any(case["expect"] in str(x) for x in f):
                problems.append(f"invalid fixture failed for another reason: {path}\n    expected '{case['expect']}', got:\n    " + "\n    ".join(map(str, f)))
    for p in problems:
        print(p)
    print(f"{'FAIL' if problems else 'PASS'}: {n} fixture(s), {len(problems)} problem(s)")
    return 1 if problems else 0


def cmd_compat(args: argparse.Namespace) -> int:
    changes = compat.compare_dirs(Path(args.old), Path(args.new))
    breaking = [c for c in changes if c.breaking]
    for c in changes:
        if c.breaking or args.verbose:
            print(c)
    print(f"{'FAIL' if breaking else 'PASS'}: {len(breaking)} breaking change(s), {len(changes) - len(breaking)} compatible change(s)")
    return 1 if breaking else 0


def cmd_canonicalize(args: argparse.Namespace) -> int:
    doc = json.loads(Path(args.file).read_text(encoding="utf-8"))
    if args.without:
        doc = crypto.without(doc, *args.without)
    sys.stdout.buffer.write(canonicalize(doc) + b"\n")
    return 0


def cmd_params_hash(args: argparse.Namespace) -> int:
    doc = json.loads(Path(args.file).read_text(encoding="utf-8"))
    action = doc.get("action", doc) if "proposal_id" in doc else doc
    print(crypto.params_hash(action))
    return 0


def cmd_schemas(args: argparse.Namespace) -> int:
    print(f"spec {SPEC_VERSION} ({schema_dir()})")
    for name in schema_names():
        print(f"  {name}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="autonomy-conformance", description=__doc__)
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__} (spec {SPEC_VERSION})")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("check", help="check a component: manifest, conformance level and sample messages")
    p.add_argument("--manifest", required=True)
    p.add_argument("--samples", help="directory of sample documents (.json or .jsonl)")
    p.add_argument("--level", choices=sorted(LEVELS))
    p.add_argument("--keys", help="JSON file mapping component refs to ed25519 public keys; enables signature checks")
    p.add_argument("--strict", action="store_true", help="treat warnings as failures")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("validate", help="validate files against one schema")
    p.add_argument("schema")
    p.add_argument("files", nargs="+")
    p.add_argument("--keys")
    p.add_argument("--strict", action="store_true")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("selftest", help="run the spec's own fixtures")
    p.add_argument("--fixtures", help="fixtures/v0 directory (default: the one next to the package)")
    p.set_defaults(func=cmd_selftest)

    p = sub.add_parser("compat", help="report breaking changes between two schema directories of the same MAJOR")
    p.add_argument("--old", required=True)
    p.add_argument("--new", required=True)
    p.add_argument("-v", "--verbose", action="store_true", help="also list compatible changes")
    p.set_defaults(func=cmd_compat)

    p = sub.add_parser("canonicalize", help="print the RFC 8785 canonical form of a JSON file")
    p.add_argument("file")
    p.add_argument("--without", nargs="*", help="drop these top-level fields first (e.g. signature)")
    p.set_defaults(func=cmd_canonicalize)

    p = sub.add_parser("params-hash", help="params_hash of a proposal (or of a bare action object)")
    p.add_argument("file")
    p.set_defaults(func=cmd_params_hash)

    p = sub.add_parser("schemas", help="list bundled schemas")
    p.set_defaults(func=cmd_schemas)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
