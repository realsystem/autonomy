"""Lab authority: the owner's keys, a signed registry and a signed policy bundle for ONE node.

    python make_authority.py OUT_DIR

Writes, in OUT_DIR (keys are lab-only and never leave it):
    owner-root.key, owner-ops.key          private keys, mode 600
    registry/owner-root.pub, registry/registry.json, registry/registry.sig.json
                                           what a node's RegistryStore pins and verifies
    policy-bundle.json                     signed bundle: an A1 grant for agent:node-assistant

The grant is A1 on host.restart_service for the services listed below, so the assistant's
restart proposal is permitted by policy but still waits for the owner (A1 does not cover R1).
Run again to renew; the keys are kept, the bundle and registry are re-signed with a newer version.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from autonomy_core import (
    canonicalize,
    encode_public_key,
    generate_private_key,
    load_private_key,
    save_private_key,
    sign,
    sign_object,
)
from autonomy_core.registry import sign_owners, sign_registry
from autonomy_core.ids import to_rfc3339

SERVICES = ["frigate"]
DAYS = 30


def key(path: Path):
    if path.exists():
        return load_private_key(path)
    k = generate_private_key()
    save_private_key(k, path)
    os.chmod(path, 0o600)
    return k


def write(path: Path, doc) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    os.chmod(path, 0o644)


def main(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    now = time.time()
    root, ops = key(out / "owner-root.key"), key(out / "owner-ops.key")

    owners = sign_owners(
        {
            "ops": [
                {"key": encode_public_key(ops), "expires": to_rfc3339(now + 365 * 86400), "comment": "lab"}
            ]
        },
        root,
    )
    registry = {
        "registry_format": 1,
        "schema_version": 1,
        "owners": owners,
        "nodes": {},
        "components": {},
        "revoked": [],
    }
    serial = int(now)
    sig = sign_registry(registry, ops, serial=serial, signer="owner:ops", now=now)
    write(out / "registry" / "registry.json", registry)
    write(out / "registry" / "registry.sig.json", sig)
    (out / "registry" / "owner-root.pub").write_text(encode_public_key(root) + "\n")
    os.chmod(out / "registry" / "owner-root.pub", 0o644)

    grant = {
        "grant_id": "grt_lab_node_assistant",
        "grantee": "agent:node-assistant",
        "responsibility": "node.assistance",
        "autonomy_level": "A1",
        "actions": ["host.restart_service"],
        "resources": {"services": SERVICES},
        "constraints": {"max_per_day": 20, "max_risk_class": "R1"},
        "requires_verification": True,
        "valid_from": to_rfc3339(now - 60),
        "expires": to_rfc3339(now + DAYS * 86400),
        "issued_by": "owner:ops",
    }
    sign_object(grant, "grant", ops)
    bundle = {
        "bundle": time.strftime("%Y.%m.%d", time.gmtime(now)) + f"-{int(now) % 100000}",
        "issued_at": to_rfc3339(now),
        "policies": [
            {
                "id": "node-assistant-restart",
                "description": "the node assistant may propose restarts of the services in its grant",
                "cedar": 'permit (principal == Agent::"node-assistant", action == Action::"host.restart_service", '
                'resource in Group::"lab_services")\nwhen { context has grant };',
            },
            {
                "id": "no-r3",
                "description": "R3 is never permitted remotely",
                "cedar": 'forbid (principal, action, resource) when { context.risk_class == "R3" };',
            },
        ],
        "groups": {"lab_services": SERVICES},
        "grants": [grant],
        "signer": "owner:ops",
        "key": encode_public_key(ops),
    }
    bundle["signature"] = sign(ops, canonicalize({k: v for k, v in bundle.items() if k != "signature"}))
    write(out / "policy-bundle.json", bundle)
    print(
        f"lab authority in {out}: bundle {bundle['bundle']}, grant A1 for agent:node-assistant on {', '.join(SERVICES)}"
    )


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(Path(sys.argv[1]))
