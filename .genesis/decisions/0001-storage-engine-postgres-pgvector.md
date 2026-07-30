# ADR 0001 — Storage Engine: PostgreSQL + pgvector

- **Date:** 2026-07-30
- **Status:** accepted
- **Phase / milestone:** Phase 14 Data Engineering · M2
- **Source:** `../../../cmis-memory/remex_ai_system_design_v1.md` → "ADR 0001"

## Context
CMIS needs durable storage for typed memory records plus vector similarity search for retrieval, under a
self-hosted deployment constraint.

## Decision
Use PostgreSQL with the pgvector extension as the single storage engine for both structured memory records
and their embeddings.

## Consequences
- Positive: one engine, ACID transactions, unified backup/restore, row-level security available natively
  for tenant isolation.
- Positive: simpler self-hosted Docker Compose topology (one stateful service).
- Negative / cost: pgvector's ANN performance ceiling is lower than a purpose-built vector store at very
  high scale — acceptable for this project's scope; revisit only if retrieval latency measurably degrades
  under real load.
- **Invariant added to context-graph.json:** `tenant_isolation_is_db_enforced` depends on this choice
  (RLS is PostgreSQL-specific).

## Alternatives rejected
- A dedicated vector database (standalone ANN-optimized store) — better retrieval-quality ceiling at very
  large scale, but adds a second system to operate, back up, and secure.
