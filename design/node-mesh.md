# Node mesh: independent nodes that watch and ask each other

**Status:** Draft · 2026-11-13 · decision: [ADR-018](adr/ADR-018-independent-nodes-peer-mesh-and-owner-totp.md)

Contract: `spec/schemas/v0/peer-request.schema.json` (contract `peer`), carried as the `control.command_request` envelope of the fleet control plane; Python implementation `autonomy_core.peer` (`RequestGuard`). The Go side is not built yet.

## 1. Shape

A site has two kinds of node, and each works alone:

| Node | Runs | Telegram bot | Reached by the peer |
|---|---|---|---|
| **HA node** (Raspberry Pi, HAOS) | Home Assistant, plus the `autonomy-node` add-on (bot + Field Station) | its own token, in the add-on | the add-on's port, on the LAN or Tailscale |
| **Edge node** (Linux box) | `edge_server` (Frigate, bot, optional processor) | its own token | the bot's peer port, on the LAN or Tailscale |

Nothing here names an address: each node's config lists its peers (`PEERS` file: id, URL, public key).

## 2. Status: the fleet heartbeats

Status and silence alerts are not a new mechanism. Each node already sends signed heartbeats to its peers over the fleet control plane (`edge_server/telegram-bot/controlplane.py`), shows them in `/fleet`, and alerts when a node has been silent for `fleet.suspect_after`. The HA node joins the fleet as an `ha-addon` node (edge-system.md, "Roles and packaging"), so the Pi and the edge box watch each other with the same code. Nothing new is published.

## 3. Requests: sign, check, then run through the receiver's own path (`control.command_request`)

A request to act on the peer (restart a service, reboot) is a signed envelope: sender, target, action, parameters, time, nonce, and **the owner's TOTP code**.

The receiver:
1. checks the signature against the registry and rejects a replayed nonce or an old timestamp;
2. checks that the sender holds a grant for the action (grants are narrow: restart a named service, never a shell);
3. **verifies the TOTP code against its own secret** (single use per 30 s window, same as today);
4. runs the action through its normal governed path (policy, approval rules, audit entry that names the sender).

Without a valid code at step 3 nothing runs, whatever the sender's key allows. A node cannot generate a code, so no automated path (an agent, a script, a compromised peer) can restart or reboot the other node.

Reboots and restarts have a per-action cooldown, so two nodes cannot trigger each other in a loop.

## 4. What each node does for the other

- **On the HA node:** restart HA core, reboot the host, through the Supervisor API (the add-on's own token), behind the add-on bot's TOTP gate.
- **On the edge node:** the existing governed helpers (`/restart`, `/reboot`, `/redeploy`).

## 5. Tests (lab)

Two lab nodes (the stand-in HA node and the edge). Scenarios: a request without a code, with a wrong code, with a reused code or sent late is refused; a replayed envelope is refused; a request outside the sender's grant is refused; the cooldown stops a second restart; each node keeps working with the other down.

## 6. Out of scope for now

Fleet-wide registries beyond two nodes, NATS transport, a hub.
