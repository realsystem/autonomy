"""Loading the v0 schemas and validating instances against them."""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterator

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

SPEC_VERSION = "0.5.0"
MAJOR = 0
BASE_URI = f"https://autonomy.local/spec/v{MAJOR}/"

# Contract name (as used in manifest "contracts") -> schemas it covers.
CONTRACTS = {
    "envelope": ["envelope"],
    "health": ["health", "ready", "cell-status"],
    "error": ["error"],
    "logging": ["log-record"],
    "action-lifecycle": [
        "grant", "policy-bundle", "action-proposal", "evaluation", "decision-request", "decision",
        "approval", "control-order", "action-request", "execution-record", "verification",
    ],
    "audit": ["audit-record"],
    "job": ["job"],
    "registry": ["registry", "registry-signature"],
}

# Conformance level -> contracts the manifest must declare.
LEVELS = {
    "L0": ["envelope", "health"],
    "L1": ["envelope", "health", "action-lifecycle", "audit"],
    "L2": ["envelope", "health", "job"],
}

# Envelope type -> schema its data must use.
TYPE_SCHEMAS = {
    "action.proposed": "action-proposal",
    "action.evaluated": "evaluation",
    "action.decided": "decision",
    "action.approved": "approval",
    "action.requested": "action-request",
    "action.accepted": "execution-record",
    "action.rejected": "execution-record",
    "action.started": "execution-record",
    "action.succeeded": "execution-record",
    "action.failed": "execution-record",
    "action.cancelled": "execution-record",
    "action.verified": "verification",
    "authority.granted": "grant",
    "system.suspended": "control-order",
    "system.resumed": "control-order",
    "health.reported": "health",
    "job.queued": "job",
    "job.leased": "job",
    "job.started": "job",
    "job.succeeded": "job",
    "job.failed": "job",
    "job.dead_lettered": "job",
    "job.cancelled": "job",
}


def schema_dir() -> Path:
    """Schemas bundled with the package, overridable with AUTONOMY_SPEC_SCHEMAS."""
    env = os.environ.get("AUTONOMY_SPEC_SCHEMAS")
    if env:
        return Path(env)
    here = Path(__file__).resolve().parent
    for candidate in (here / "schemas" / f"v{MAJOR}", here.parent / "schemas" / f"v{MAJOR}"):
        if candidate.is_dir():
            return candidate
    raise FileNotFoundError("cannot find the v0 schemas; set AUTONOMY_SPEC_SCHEMAS")


def schema_names() -> list[str]:
    return sorted(p.name[: -len(".schema.json")] for p in schema_dir().glob("*.schema.json"))


@lru_cache(maxsize=None)
def _registry() -> Registry:
    resources = []
    for p in sorted(schema_dir().glob("*.schema.json")):
        doc = json.loads(p.read_text(encoding="utf-8"))
        resources.append((doc["$id"], Resource.from_contents(doc)))
    return Registry().with_resources(resources)


@lru_cache(maxsize=None)
def validator(name: str) -> Draft202012Validator:
    uri = BASE_URI + f"{name}.schema.json"
    try:
        schema = _registry().contents(uri)
    except Exception as e:  # referencing raises its own NoSuchResource
        raise KeyError(f"unknown schema {name!r}") from e
    return Draft202012Validator(schema, registry=_registry(), format_checker=FormatChecker())


def schema_for_uri(uri: str) -> str | None:
    """Map a dataschema URI to a spec schema name, or None if it is not a spec URI."""
    if not uri.startswith(BASE_URI) or not uri.endswith(".schema.json"):
        return None
    return uri[len(BASE_URI): -len(".schema.json")]


def schema_errors(name: str, instance: Any) -> Iterator[tuple[str, str]]:
    """Yield (json-path, message) for each schema violation."""
    for err in sorted(validator(name).iter_errors(instance), key=lambda e: list(e.absolute_path)):
        path = "$" + "".join(f"[{p}]" if isinstance(p, int) else f".{p}" for p in err.absolute_path)
        yield path, f"{err.message} ({err.validator})"
