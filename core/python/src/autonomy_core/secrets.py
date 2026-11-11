"""Secrets (C18): ``secret://`` references and the secret scanner.

Configuration never holds a secret value, only a reference, resolved at start-up:

    secret://env/TELEGRAM_BOT_TOKEN           environment variable
    secret://file/run/secrets/ha_token         a file (must not be group/world readable)
    secret://sops/config/secrets.yaml#ha.token SOPS-encrypted file (sops in PATH), dotted key
    secret://age/secrets/ha.age                age-encrypted file (AGE_IDENTITY or ~/.config/age/key.txt)
    secret://store/ha_token                    a provider registered by the host (edge_server's store)

Every resolved value is registered with the log redactor. ``scan`` finds secret-shaped
text in files (git trees, logs, envelopes, recorded prompts): IT-81.
"""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import Reject
from .telemetry import PATTERNS, REDACTOR, Redactor

SCHEME = "secret://"
_REF = re.compile(r"^secret://(env|file|sops|age|store)/(.+)$")
_providers: dict[str, Callable[[str], str]] = {}


def register_provider(name: str, fn: Callable[[str], str]) -> None:
    """``secret://store/<name>`` lookups go to ``fn(name)`` (name is the part after store/)."""
    _providers[name] = fn


def is_ref(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(SCHEME)


def resolve(ref: str, *, redactor: Redactor = REDACTOR, env: Mapping[str, str] | None = None) -> str:
    """The secret a reference points to. Raises Reject("secret unavailable")."""
    m = _REF.match(ref)
    if not m:
        raise Reject("malformed", f"not a secret reference: {ref[:40]}")
    kind, rest = m.groups()
    try:
        value = _RESOLVERS[kind](rest, env if env is not None else os.environ)
    except Reject:
        raise
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        raise Reject("secret unavailable", f"{kind}/{rest.split('#')[0]}: {type(exc).__name__}") from None
    redactor.register(value)
    return value


def resolve_config(config: Any, **kw: Any) -> Any:
    """A copy of ``config`` with every ``secret://`` string replaced by its value."""
    if is_ref(config):
        return resolve(config, **kw)
    if isinstance(config, Mapping):
        return {k: resolve_config(v, **kw) for k, v in config.items()}
    if isinstance(config, list):
        return [resolve_config(v, **kw) for v in config]
    return config


def _env(name: str, env: Mapping[str, str]) -> str:
    if name not in env or env[name] == "":
        raise Reject("secret unavailable", f"env/{name} is not set")
    return env[name]


def _file(path: str, env: Mapping[str, str]) -> str:
    p = Path("/" + path.lstrip("/"))
    mode = p.stat().st_mode
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise Reject("secret unavailable", f"{p} is readable by others (chmod 600)")
    return p.read_text(encoding="utf-8").strip()


def _sops(rest: str, env: Mapping[str, str]) -> str:
    path, _, key = rest.partition("#")
    out = subprocess.run(
        ["sops", "--decrypt", "--output-type", "json", path],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    value: Any = json.loads(out.stdout)
    for part in key.split(".") if key else []:
        value = value[part]
    if not isinstance(value, str):
        raise ValueError("the key does not hold a string")
    return value


def _age(path: str, env: Mapping[str, str]) -> str:
    identity = env.get("AGE_IDENTITY") or str(Path.home() / ".config" / "age" / "key.txt")
    out = subprocess.run(
        ["age", "--decrypt", "-i", identity, path], capture_output=True, text=True, timeout=30, check=True
    )
    return out.stdout.strip()


def _store(name: str, env: Mapping[str, str]) -> str:
    for provider in _providers.values():
        try:
            value = provider(name)
        except KeyError:
            continue
        if value:
            return value
    raise Reject("secret unavailable", f"store/{name} not found")


_RESOLVERS: dict[str, Callable[[str, Mapping[str, str]], str]] = {
    "env": _env,
    "file": _file,
    "sops": _sops,
    "age": _age,
    "store": _store,
}


# --- scanner ------------------------------------------------------------------------------


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    rule: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.rule}"


SKIP_DIRS = {
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "dist",
    "build",
}
SKIP_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".ico",
    ".pdf",
    ".zip",
    ".gz",
    ".tgz",
    ".whl",
    ".so",
    ".pyc",
    ".woff",
    ".woff2",
}
ALLOW_MARK = "secret-scan: allow"


def load_allowlist(path: Path) -> list[tuple[str, str]]:
    """Lines ``<glob> [rule]``: findings in matching files (for that rule) are expected test data."""
    out: list[tuple[str, str]] = []
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                glob, _, rule = line.partition(" ")
                out.append((glob, rule.strip()))
    return out


def _allowed(rel: str, rule: str, allow: list[tuple[str, str]]) -> bool:
    from fnmatch import fnmatch

    return any(fnmatch(rel, g) and (not r or r == rule) for g, r in allow)


def scan_text(text: str, path: str = "<text>") -> list[Finding]:
    found: list[Finding] = []
    for n, line in enumerate(text.splitlines(), 1):
        if ALLOW_MARK in line:
            continue
        for rule, rx in PATTERNS:
            if rx.search(line):
                found.append(Finding(path, n, rule))
    for rule, rx in PATTERNS[:1]:  # multi-line private key blocks
        for m in rx.finditer(text):
            n = text.count("\n", 0, m.start()) + 1
            if ALLOW_MARK not in text.splitlines()[n - 1] and Finding(path, n, rule) not in found:
                found.append(Finding(path, n, rule))
    return found


def _relative(f: Path, base: Path) -> str:
    try:
        return str(f.resolve().relative_to(base.resolve()))
    except ValueError:
        return ""


def scan(
    paths: Iterable[Path | str], *, allowlist: Path | None = None, root: Path | None = None
) -> list[Finding]:
    """Findings in files under ``paths`` (directories are walked)."""
    allow = load_allowlist(allowlist) if allowlist else []
    base = root or Path.cwd()
    findings: list[Finding] = []
    for start in paths:
        p = Path(start)
        files = (
            [p]
            if p.is_file()
            else [f for f in p.rglob("*") if f.is_file() and not (set(f.parts) & SKIP_DIRS)]
        )
        for f in sorted(files):
            if f.suffix.lower() in SKIP_SUFFIXES or f.stat().st_size > 5_000_000:
                continue
            try:
                text = f.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            rel = _relative(f, base) or _relative(f, p if p.is_dir() else p.parent) or str(f)
            findings += [x for x in scan_text(text, rel) if not _allowed(rel, x.rule, allow)]
    return findings


# --- SOPS (ADR-013) ------------------------------------------------------------------------

_SOPS_VALUE = re.compile(
    r"^ENC\[AES256_GCM,data:[A-Za-z0-9+/=]*,iv:[A-Za-z0-9+/=]+,tag:[A-Za-z0-9+/=]+,type:[a-z]+\]$"
)


def check_sops(doc: Any) -> list[str]:
    """Problems with a document that must be SOPS-encrypted for age recipients: every
    value encrypted except keys ending in ``unencrypted_suffix``. Empty: fine."""
    if not isinstance(doc, dict) or not isinstance(doc.get("sops"), dict):
        return ["no sops metadata: encrypt with `sops --encrypt --age <recipient>`"]
    meta = doc["sops"]
    problems: list[str] = []
    if not meta.get("age"):
        problems.append("sops metadata has no age recipients (ADR-013 uses age)")
    suffix = str(meta.get("unencrypted_suffix", "_unencrypted"))

    def walk(value: Any, path: str) -> None:
        if isinstance(value, dict):
            for k, v in value.items():
                if not (isinstance(k, str) and k.endswith(suffix)):
                    walk(v, f"{path}.{k}")
        elif isinstance(value, list):
            for i, v in enumerate(value):
                walk(v, f"{path}[{i}]")
        elif value is not None and not (isinstance(value, str) and _SOPS_VALUE.match(value)):
            problems.append(f"{path} is not encrypted")

    for k, v in doc.items():
        if k != "sops" and not k.endswith(suffix):
            walk(v, f"$.{k}")
    return problems
