# Knowledge and Memory

**Status:** Draft 0.1 · 2026-11-06

## 1. Five kinds of data, five owners

The brief warns against combining them into one database. They have different owners, lifecycles, consistency needs and deletion rules.

| Kind | What | Owner | Store | Consistency | Retention | Never |
|---|---|---|---|---|---|---|
| **Operational state** | Current state of jobs, actions, zones, grants | The component that acts on it (C07, C06, HA, C04) | That component's DB | Strong, local | Owner's policy | Copied as truth elsewhere |
| **Event history** | What happened, as signed envelopes | C09 on each cell | Per-cell SQLite log, archived | Append-only, per-producer order | Hot 90 d, archive 2 y (configurable per topic) | Edited |
| **Audit** | Who decided and did what, why | C16 | Hash-chained per cell | Append-only, tamper-evident | ≥ 5 y | Dropped, edited |
| **Documents** | Files, pages, transcripts, reports, manuals | C15 (or the app) | Content-addressed files + index | Immutable versions | Per space | Mixed across private spaces |
| **Structured knowledge** | Facts, entities, relations, with provenance and time | C15 | SQLite (FTS5 + vector extension) per space; Postgres later | Versioned facts | Per space | Stored without provenance |
| **Agent memory** | An agent's episodic notes and learned parameters | The agent | Agent's own store or a private C15 space | Agent-defined | Manifest-declared | Read by other agents |

## 2. Fact model

```json
{
  "fact_id": "fct_…", "space": "farm", "version": 3, "supersedes": "fct_…v2",
  "subject": "probe:wh51_ch3", "predicate": "dry_down_rate_pct_per_day", "object": {"value": 2.4, "unit": "%/day"},
  "valid_from": "2026-11-01", "valid_to": null,
  "confidence": 0.8,
  "provenance": {"source": "agent:field-station", "method": "derived", "inputs": ["evt:…", "evt:…"],
                 "retrieved_at": "2026-11-06T06:00:00Z", "trust": "trusted"},
  "created_at": "…", "created_by": "agent:field-station"
}
```

- **Temporal**: facts have validity intervals; queries specify `as_of` (default now). "What did we believe on Nov 1?" is answerable.
- **Provenance** is mandatory. For external sources: URL, publisher, retrieval time, content hash, and a **source evaluation** (`primary` / `secondary` / `user-generated`, plus a reliability note).
- **Trust** inherits from inputs (§3 of contracts). Untrusted facts may be read by trusted agents only as data, never as instructions, and any action depending on them is gated (GOV-007).
- **Versioning**: facts are never updated in place; new versions supersede.

## 3. Spaces

| Space | Writers | Readers | Example |
|---|---|---|---|
| `shared` | Owner; agents with `knowledge.write.shared` grant (R1, usually approval) | All registered agents | Site facts, equipment manuals |
| `agent:<id>` | That agent | That agent; owner | A01's learned curves |
| `farm`, `media`, … (domain) | Agents granted the domain | Agents granted the domain | Farm history |
| `private:<app>` | The application | The application only | `my_fin_structure` data, if it ever uses C15 |

Spaces are separate SQLite files, so deleting a space is deleting a file (plus backups expiring per schedule).

## 4. Retrieval

| Need | Mechanism | Why |
|---|---|---|
| Exact facts by subject/predicate | SQL | Most farm questions |
| Keyword search over documents | SQLite FTS5 | No extra service |
| Semantic search | Vector extension in the same SQLite file (e.g. `sqlite-vec`) [A: evaluate in M19], embeddings from C12 (local model by default) | Avoid a separate vector database until scale requires it |
| Time-window analytics on sensors | Home Assistant long-term statistics + weekly CSV export (farm design [V]); later DuckDB over exported Parquet | Sensor history already exists in HA |

Postgres + pgvector is the upgrade path when concurrent writers or data volume require it (`jarvis` already plans Postgres [V]); it is not the starting point. No separate vector database ([ADR-010](adr/ADR-010-sqlite-first-separate-stores.md)).

## 5. Learning from operations

The loop that makes autonomy improve without self-modifying authority:

1. Every verified action (C14) produces an **outcome record** linking proposal → decision → execution → verification.
2. Agents read outcome records of their own proposals and update their *own* parameters (agent memory), e.g. "climate factor changes > 15 % were followed by contradicted soil response twice".
3. Agents can propose **lessons** to `shared` and **bound changes** to the owner — never apply them to their own grants (GOV-024).
4. Monthly report (OPS-010) summarises outcomes per agent; promotion/demotion evidence comes from here.

## 6. Retention and deletion

| Data | Default | Deletion |
|---|---|---|
| Event history | 90 d hot, 2 y archive | Per topic; private topics shorter |
| Audit | 5 y | Not deletable by agents; owner can export and archive |
| Documents | Per space | Delete version or whole space; backups expire in ≤ 35 d |
| Facts | Kept (superseded versions kept 1 y) | Owner deletion request removes all versions and derived embeddings |
| Agent memory | Manifest-declared | Agent retirement deletes store after export |
| LLM prompts/responses | Not stored by default; sampled traces 14 d with redaction | — |
