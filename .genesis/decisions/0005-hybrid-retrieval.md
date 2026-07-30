# ADR 0005 — Hybrid Retrieval (Vector + Keyword + Graph)

- **Date:** 2026-07-30
- **Status:** accepted
- **Phase / milestone:** Phase 6 Memory Architecture · M7
- **Source:** `../../../cmis-memory/remex_ai_system_design_v1.md` → "ADR 0005"

## Context
Vector similarity alone misses exact-term/name lookups and relational context; keyword alone misses
paraphrase/semantic matches.

## Decision
Combine vector similarity (pgvector), Postgres full-text keyword search (`tsvector`), and a lightweight
entity-link table (graph signal) at retrieval time, fused by the Ranking Service.

## Consequences
- Positive: each signal covers the others' blind spots — more production-robust recall than any single index.
- Negative / cost: three indexes to maintain and a fusion/ranking step whose weights require ongoing tuning
  ("art, not science" — flagged as a risk in the design doc §16).
- **Invariant added to context-graph.json:** `read_path_makes_no_external_calls` — all three signals must
  stay local to hold the <150ms p95 budget.

## Alternatives rejected
- Vector-only retrieval — simpler, single index, but demonstrably misses exact-match and relational queries
  that keyword/graph signals catch. (Kept deliberately as the M3 baseline to measure against.)
- A dedicated graph database (e.g. Neo4j) for the graph signal — deferred; a Postgres entity-link table is
  sufficient until relational queries prove too slow or complex for SQL.
