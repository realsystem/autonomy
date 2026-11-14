# Node mesh: independent nodes that watch and ask each other

**Status:** Draft · 2026-11-13 · decision: [ADR-018](adr/ADR-018-independent-nodes-peer-mesh-and-owner-totp.md)

## 1. Shape

A site has two kinds of node, and each works alone:

| Node | Runs | Telegram bot | Reached by the peer |
|---|---|---|---|
| **HA node** (Raspberry Pi, HAOS) | Home Assistant, plus the `autonomy-node` add-on (bot + Field Station) | its own token, in the add-on | the add-on's port, on the LAN or Tailscale |
| **Edge node** (Linux box) | `edge_server` (Frigate, bot, optional processor) | its own token | the bot's peer port, on the LAN or Tailscale |

Nothing here names an address: each node's config lists its peers (`PEERS` file: id, URL, public key).

## 2. Status: each node publishes, the peer polls

- A node serves a small signed document: node id, time, a monotonic counter, the services it watches and their state, and the age of the Field Station snapshot if there is one. It carries no secrets and no camera data.
- The peer polls it on an interval. The signature is checked against the registered public key. A stale counter or a bad signature counts as silence.
- After `PEER_STALE_SECONDS` of silence the polling bot alerts its owner ("<node> not reporting"), then sends a recovery message. This reuses the Field Station alert logic and its hysteresis.
- `/peer` in either bot shows the other node's last document.

## 3. Requests: sign, check, then run through the receiver's own path

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

Two lab nodes (the stand-in HA node and the edge). Scenarios: status is published and polled; silence raises one alert and one recovery; a request without a code, with a wrong code, with a reused code or sent late is refused; a replayed envelope is refused; a request outside the sender's grant is refused; the cooldown stops a second restart; each node keeps working with the other down.

## 6. Out of scope for now

Fleet-wide registries beyond two nodes, NATS transport, a hub.
