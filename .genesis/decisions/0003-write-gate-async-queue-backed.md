# ADR 0003 — Write Gate Runs Async, Queue-Backed

- **Date:** 2026-07-30
- **Status:** accepted
- **Phase / milestone:** Phase 4 Workflow Orchestration · M5
- **Source:** `../../../cmis-memory/remex_ai_system_design_v1.md` → "ADR 0003"

## Context
The Write Gate calls open-source/free-tier LLM providers (NVIDIA NIM, Gemini free tier). These are
rate-limited and not guaranteed low-latency, unlike a dedicated paid API.

## Decision
The Write Gate runs as a queue-backed background worker (Redis + RQ/Celery), not inline on the user-facing
request path. A turn's response is generated from whatever is already stored; the candidate fact from that
turn is evaluated and admitted asynchronously afterward.

## Consequences
- Positive: user-facing latency is fully decoupled from LLM-judge latency/variance.
- Positive: natural backpressure point (bounded queue, `429` on overflow) instead of the request path
  failing under load.
- Negative / cost: memory becomes available with a short delay (seconds to tens of seconds) rather than
  immediately — acceptable because retrieval is designed to work with whatever is already committed.
- **Invariants added to context-graph.json:** `background_jobs_never_block_the_request_path` and
  `read_path_makes_no_external_calls`.

## Alternatives rejected
- Synchronous inline call on the request path — simpler control flow, but ties user-facing latency directly
  to a rate-limited external provider's response time, violating the <150ms p95 read-path budget.
