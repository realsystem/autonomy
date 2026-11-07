# ADR-009: MCP for tool exposure behind the gateway; A2A deferred

**Status:** Accepted · 2026-11-06

## Context

LLM tooling increasingly speaks MCP; MCP 2026-07-28 is stateless and moves long tasks to an extension [R]. A2A v1.0 targets inter-organisation agent interop [R].

## Decision

Adapters may expose R0 tools via MCP. R1+ tools are exposed only by C06's MCP facade and create proposals. Internal coordination uses Autonomy envelopes, not A2A. An A2A boundary adapter may be added for external agents.

## Alternatives

| Alternative | Why not (now) |
|---|---|
| MCP everywhere including authorization | MCP's auth model is per-server OAuth, not our grant model |
| A2A internally | Duplicates our action lifecycle; adds a second task model |

## Consequences

LLM clients see tools naturally; authority stays in C04/C06.

## Evidence and references

MCP release notes and summaries; A2A spec summary [R]. See [research-and-references.md](../research-and-references.md).

## Revisit when

An external agent ecosystem the owner wants to join.
