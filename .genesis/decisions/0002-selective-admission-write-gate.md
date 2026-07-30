# ADR 0002 — Selective Admission via a Write Gate

- **Date:** 2026-07-30
- **Status:** accepted
- **Phase / milestone:** Phase 5 LLM & Reasoning Layer · M5
- **Source:** `../../../cmis-memory/remex_ai_system_design_v1.md` → "ADR 0002"

## Context
Storing every turn verbatim pollutes the memory store, hurts retrieval precision, and grows storage/decay
burden without bound.

## Decision
All candidate facts pass through a Write Gate (LLM-judge based evaluator) that decides
admit / reject / update-existing before anything is persisted.

## Consequences
- Positive: prevents memory pollution at the source; sets an importance signal at write time that ranking
  can use later.
- Negative / cost: introduces LLM-judge cost and variance on the write path — mitigated by making the
  Write Gate asynchronous (ADR 0003) so this cost never touches user-facing latency.
- **Invariant added to context-graph.json:** `no_durable_write_without_provenance_and_pii_verdict` — the
  gate's decision trace is part of every stored record.

## Alternatives rejected
- Store everything, filter only at retrieval time — simpler, but pushes the precision problem downstream
  and never bounds storage growth. (This is Approach A in the `PLAN.md` brainstorm.)
