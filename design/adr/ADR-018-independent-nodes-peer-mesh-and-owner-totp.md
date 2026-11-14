# ADR-018: Independent nodes, a Home Assistant node managed from the edge, a peer protocol, and owner TOTP for every state change

**Status:** Accepted · 2026-11-13 · owner decision

## Context

The owner runs two sites. Each has a Raspberry Pi with Home Assistant OS (HAOS, from flash or USB) and a Linux box that runs `edge_server` (cameras). The Pi cannot run our packages: HAOS only runs add-ons. The nodes must stay useful alone: if the edge box is down, the Pi still reports and can be controlled, and the other way round. The frameworks must work in other homes, so nothing in the code may depend on this owner's addresses.

## Decision

1. **HAOS is never installed by our scripts.** The `edge_server` deploy script can still run Home Assistant in a container on an edge box (`INSTALL_HOMEASSISTANT`, optional, for standalone edges).
2. **The edge server is a jumpbox for a HAOS node.** The deploy script asks for the node's address and what to put on it, then configures it over the LAN (design: `edge_server/docs/design/ha-node.md`). It backs up first, touches only files it owns, runs `check_config` before restarting and rolls back on failure. Answers are kept in a per-site file, so reruns are repeatable.
3. **The Pi has its own Telegram bot, as an add-on** (bot plus Field Station in one add-on, so the station's `status.json` stays private to it). Each node has its own bot token and its own TOTP secret.
4. **Peers watch each other and can ask each other to act** (design: `node-mesh.md`):
   - each node publishes a signed status document; the other polls it and alerts when it stops (like the Field Station alert);
   - a request to act on the peer is signed by the sender, checked against the registry and grants, and executed through the receiver's own governed path.
5. **Owner TOTP is required for every state change** (`/reboot`, `/redeploy`, `/restart`, `/approve`, `/resume`, and any new one), on both nodes, with no exception for peers or automation. A request that crosses nodes carries the owner's code and the receiving node verifies it against its own secret. A peer can never reboot or restart anything without a fresh code. Read-only commands need no code.
6. **Site details are configuration, never code.** Addresses, tokens, keys and the list of things to deploy live in per-site files and the secret store (ADR-013).

## Consequences

- A second bot per site and a second TOTP entry in the authenticator app.
- An arm64 add-on image is needed, built in CI.
- A new contract (peer status and peer request) goes into the spec with tests. Both repos implement it.
- Host reboots and service restarts always need the owner. If both nodes are down the owner learns it from silence.
- The variable `REBOOT_TOTP_SECRET` is renamed `OWNER_TOTP_SECRET`; the old name stays as an alias.

## Revisit when

A site needs unattended restarts (then a narrow, signed pre-approval, never an exception to point 5), or a third node type appears.
