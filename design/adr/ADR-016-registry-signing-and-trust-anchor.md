# ADR-016: Registry signing, serials and the trust anchor

**Status:** Proposed · 2026-11-08

## Context

ADR-003 decided on an owner-signed registry with an offline root key delegating to an online ops key. M3 has to make that concrete: what bytes are signed, how a cell knows which root to trust, how an old registry is refused, and how this works for `edge_server` nodes whose deploy host has no crypto library.

## Decision

- **Detached signature** `<name>.sig.json` (spec `registry-signature`) over the canonical JSON of the registry; it carries `registry_hash`, `serial`, `signer` (`owner:root` or `owner:ops`) and the signing `key`.
- **Owners section signed by root.** `owners` holds the root key and the delegated ops keys with expiry, signed by root. Ops can sign registry updates but cannot change who the owners are.
- **Serial.** Cells accept only a higher serial (or the same document again) and keep the last valid registry when an update is refused.
- **Trust anchor pinned on first use.** A cell pins `owners.root` from the first registry it accepts, and its operator compares the fingerprint (`/fleet`, `autonomy fingerprint`) out of band. Root rotation requires re-pinning each cell by hand.
- **Cells get the signed JSON**, not YAML. The owner may edit YAML; `autonomy registry sign` writes the exact JSON that was hashed.
- **Verification happens on the cell** (the `edge_server` bot, which has `autonomy-core`), not on the deploy host.

## Alternatives

| Alternative | Why not (now) |
|---|---|
| Signature embedded in the registry | Needs a canonical "without signature" view of a YAML file the owner edits; detached keeps the document plain |
| Root key pinned at install by a flag | Possible later (`--owner-root`); first use with a printed fingerprint matches how node keys are already exchanged in `edge_server` |
| Timestamps instead of serials | Clock-dependent; a serial is simpler to reason about and to show |
| Verify on the deploy host | Hosts have only PyYAML and openssl; the cell is where trust matters |

## Consequences

A stolen ops key can publish registries until its delegation expires or root removes it (signing a new `owners`). A forged *first* registry on a fresh cell is accepted; the fingerprint check at enrollment is the guard. Live propagation between cells is not part of this (C09 event sync).

## Evidence and references

[ADR-003](ADR-003-ed25519-identity-owner-signed-registry.md); spec `registry` schemas; `core/python/src/autonomy_core/registry.py`; `edge_server` `telegram-bot/controlplane.py`.

## Revisit when

Several owners, hardware-backed root keys, or automatic registry propagation (C09).
