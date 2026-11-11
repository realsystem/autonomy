# Autonomy

A modular, distributed system that progressively takes over ongoing responsibilities — observing, planning, deciding within owner-granted authority, acting, verifying and recovering — while the owner keeps control of goals, permissions, data and shutdown. *Autonomy* is a temporary name.

## Contents

| Path | What |
|---|---|
| [`design/`](design/README.md) | Design package (draft 0.1): architecture, component catalog, **component contracts**, governance, roadmap, ADRs, diagrams |
| [`spec/`](spec/README.md) | Contracts v0.1.0 (milestone M1, component C01): JSON Schemas, fixtures, vectors and the `autonomy-conformance` CLI |
| [`ops/lab/`](ops/lab/README.md) | **The lab**: the whole system on a laptop in Docker with a fake model and a real or mocked Telegram, one script |
| [`core/`](core/README.md) | Core library v0.1.0 (milestone M2, component C02): envelopes, signatures, replay protection, outbox, audit chain — Python and Go |

## Start here

- [design/README.md](design/README.md) — overview, building blocks, development order
- [design/component-contracts.md](design/component-contracts.md) — the contract every component implements
- [design/development-roadmap.md](design/development-roadmap.md) — milestones M0–M23

## Related repositories

- [`farm_automation`](https://github.com/realsystem/farm_automation) — Field Station monitoring and irrigation design; the first reference deployment
- [`edge_server`](https://github.com/realsystem/edge_server), [`processor`](https://github.com/realsystem/processor), [`farm_agent`](https://github.com/realsystem/farm_agent) — existing projects that become platform components (see [repository-mapping](design/repository-mapping.md))
