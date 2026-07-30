# PLAN — remex-ai

The machine-parseable implementation plan. Mirrors the milestone table in `DONE.html` (DONE.html is the
human/visual view; this is the one loops read). Sliced so each milestone ships in one L1 BUILD pass.

> Slicing rule: a milestone must have (a) a single clear outcome, (b) an exact **demo command** that
> proves it, and (c) a freeze boundary of files it may touch. If you can't write the demo command,
> the milestone is too vague — split it.

Design source of truth: `../../cmis-memory/remex_ai_system_design_v1.md` (system design v1 + sprint plan
+ threat model + API contracts + ADRs 0001–0006). Stack: FastAPI + Postgres/pgvector + Redis(RQ) +
local `sentence-transformers` embeddings + Caddy, all self-hosted via one Docker Compose stack.

**Standing commands** (used by every gate; must stay true repo-wide):
- typecheck/lint: `docker compose run --rm api sh -c "ruff check . && mypy app worker"`
- tests: `docker compose run --rm api pytest -q`
- invariants: `docker compose run --rm api pytest -q tests/invariants`

---

## Brainstorm (G0.5 — fill before slicing milestones)

> Three fundamentally different approaches to the cognitive job. Pick one. Record the rationale.
> This is the cheapest design decision — you haven't written a line of code yet.

### Approach A — Store-Everything, Filter At Read
Persist every turn verbatim and do all the intelligence at retrieval time: embed the whole transcript,
chunk it, and let a reranker pick what matters for the current turn. No admission decision at all.
- Strengths: simplest write path (no LLM judge, no queue, no admit/reject semantics to get wrong); nothing is ever lost, so a bad admission policy can't destroy information.
- Weaknesses: the store grows without bound and retrieval precision decays as it grows (the pollution problem just moves downstream); there is no importance signal captured at write time, so ranking has less to work with and "why do you know this?" has no decision trace to show.

### Approach B — Selective Admission With An Async Write Gate (typed store + hybrid retrieval)
An LLM judge decides admit / reject / update-existing for each candidate fact, running as a queue-backed
background worker; admitted facts are stored as typed records (episodic/semantic/procedural) with
provenance in Postgres+pgvector, and reads use vector+keyword+graph retrieval fused by a ranking service.
- Strengths: selectivity is enforced at the source, so the store stays small and retrieval precision holds as the system ages; the judge's decision trace gives explainability, an importance signal for ranking, and a natural place for conflict resolution (supersede) and PII filtering.
- Weaknesses: many more moving parts (queue, worker, provider fallback, decay, reflection) and therefore more operational surface; admission quality inherits the variance of a lighter-weight free-tier judge model, and memory becomes available seconds-to-minutes after the turn rather than immediately.

### Approach C — Model-Managed Memory (long context + memory tools, no separate store)
Give the LLM memory read/write tools and let it manage its own memory: it decides when to save, what to
save, and what to fetch, backed by a thin key-value/file store and a large context window.
- Strengths: very little infrastructure to build and the memory policy improves for free as models improve; the agent can reason about its own memory in-line, so no separate ranking service is needed.
- Weaknesses: the research base says this is exactly what models are bad at — mid-context blindness (*Lost in the Middle*) and proactive interference (*Unable to Forget*) mean self-managed memory degrades silently and non-reproducibly; there is no audit trail, no enforced tenant isolation, and no way to guarantee a user can see/correct/delete what was stored.

### Chosen: Approach B — it is the only option that keeps precision as the store grows *and* produces the decision trace that the trust requirements (explainability, correction, deletion, tenant isolation) depend on; the extra operational surface is paid for once, whereas A's precision decay and C's silent degradation get worse forever.

---

## Milestones

### M1 — Compose skeleton + health check
- **Outcome:** `docker compose up` brings up Postgres+pgvector, Redis, the FastAPI service, and a **separate worker service** (no-op loop in M1, real Write Gate consumer in M5); `GET /v1/health` reports the real status of each dependency (no stubs), including the worker's heartbeat.
- **Phase (swe-master):** Phase 13 Infrastructure & Deployment (+ Phase 3 Backend/API)
- **Files / freeze boundary:** `docker-compose.yml`, `Dockerfile`, `requirements.txt`, `app/config.py`, `app/api/main.py`, `app/api/routes_health.py`, `app/db/session.py`, `worker/main.py`, `worker/heartbeat.py`, `tests/acceptance/test_m1_health.py`, `tests/invariants/test_api_worker_separation.py`, `Makefile`, `.env.example`
- **Demo command:** `docker compose up -d --wait && docker compose run --rm api pytest -q tests/acceptance/test_m1_health.py tests/invariants/test_api_worker_separation.py`
- **Success criteria:** exit code 0; the health payload asserts postgres reachable, `vector` extension present, redis `PING` → `PONG`, and a worker heartbeat key in Redis newer than its interval; api and worker run as two services from one image with different commands; `app/api/**` imports nothing from `worker/**` (invariant test); every service defined in compose, nothing installed on the host.
- **Loops:** L1, L4
- **Skills:** canon + tdd + production-readiness, distributed-systems
- **Token budget:** 50000
- **Scope note (user decision, 2026-07-30):** the worker ships in M1 as a no-op heartbeat container rather than waiting for M5, so `background_jobs_never_block_the_request_path` is structurally true from the first milestone instead of being retrofitted.

### M2 — Typed memory schema + migrations + row-level security
- **Outcome:** the memory data model (episodic/semantic/procedural + provenance + importance/decay weights + status + `supersedes`) and the audit table exist as migrations, with RLS enabled on every memory-bearing table from day one.
- **Phase:** Phase 14 Data Engineering (+ Phase 11 Security Architecture)
- **Files:** `migrations/**`, `app/db/models.py`, `app/db/session.py`, `app/domain/memory.py`, `tests/acceptance/test_m2_rls.py`, `tests/invariants/test_rls_enforced.py`
- **Demo command:** `docker compose run --rm api sh -c "alembic upgrade head && pytest -q tests/acceptance/test_m2_rls.py tests/invariants/test_rls_enforced.py"`
- **Success criteria:** tenant B's seeded rows are provably unreadable with tenant A's credentials (automated, not manual); a deliberately unscoped `SELECT` still returns zero cross-tenant rows; `alembic downgrade -1 && alembic upgrade head` is clean (migrations rerunnable).
- **Loops:** L1, L4
- **Skills:** canon + tdd + data-systems-engineering, security-engineering
- **Token budget:** 50000

### M3 — Naive write + vector retrieval baseline (no Write Gate yet)
- **Outcome:** `POST /v1/memories` (direct, unfiltered) and `GET /v1/memories:retrieve` working with local `sentence-transformers` embeddings and vector-only search — the deliberate naive baseline later milestones must beat.
- **Phase:** Phase 6 Memory Architecture
- **Files:** `app/embedding/local_encoder.py`, `app/retrieval/vector.py`, `app/api/routes_memories.py`, `app/api/routes_retrieve.py`, `app/api/deps_auth.py`, `evals/**`, `tests/acceptance/test_m3_baseline.py`
- **Demo command:** `docker compose run --rm api sh -c "pytest -q tests/acceptance/test_m3_baseline.py && python -m evals.run --suite baseline --out evals/reports/baseline.json && python -m evals.show evals/reports/baseline.json"`
- **Success criteria:** a turn stored in one request is retrieved in the next; `evals/reports/baseline.json` exists and records precision/recall/precision@k on the labeled conversation set — this file is the comparison target for M5 and M7.
- **Loops:** L1, L3 (research: embedding model choice), L4
- **Skills:** canon + tdd + llmops-ai-agents, data-systems-engineering
- **Token budget:** 50000

### M4 — Read path fails open + token budgeting + citations
- **Outcome:** the retrieve endpoint never blocks a response: it returns `200` with `{"memories": [], "degraded": true}` on any internal failure, packs results into the caller's token budget, places highest-ranked items at the head/tail (mid-context blindness), and cites `source_turn_ids` per item.
- **Phase:** Phase 12 Reliability Engineering (+ Phase 6)
- **Files:** `app/context/budgeter.py`, `app/api/routes_retrieve.py`, `tests/acceptance/test_m4_fail_open.py`, `tests/invariants/test_read_path_fails_open.py`, `tests/invariants/test_read_path_local_only.py`
- **Demo command:** `docker compose run --rm api pytest -q tests/acceptance/test_m4_fail_open.py tests/invariants/test_read_path_fails_open.py tests/invariants/test_read_path_local_only.py`
- **Success criteria:** with Postgres forced unreachable the endpoint still answers `200 degraded:true` inside its timeout; no response ever exceeds the requested `token_budget`; every returned item carries provenance; the read path makes zero external network calls.
- **Loops:** L1, L4
- **Skills:** canon + tdd + production-readiness
- **Token budget:** 50000

### M5 — Async Write Gate (queue-backed) + pre-send PII filter
- **Outcome:** `POST /v1/memories:evaluate` returns `202 {job_id}` immediately; a Redis+RQ worker runs the LLM judge (NIM / Gemini free tier with local-model fallback) and commits admitted facts; the PII filter runs **before** any candidate text leaves the perimeter.
- **Phase:** Phase 4 Workflow Orchestration + Phase 5 LLM & Reasoning Layer
- **Files:** `worker/**`, `app/api/routes_memories.py` (evaluate + job status), `app/domain/policy.py`, `tests/acceptance/test_m5_write_gate.py`, `tests/invariants/test_provenance_and_pii_precedence.py`, `tests/invariants/test_api_worker_separation.py` (introduced in M1 — extend, don't rewrite)
- **Demo command:** `docker compose run --rm api sh -c "pytest -q tests/acceptance/test_m5_write_gate.py tests/invariants/test_provenance_and_pii_precedence.py tests/invariants/test_api_worker_separation.py && python -m evals.run --suite write_gate --out evals/reports/write_gate.json --compare evals/reports/baseline.json"`
- **Success criteria:** admission precision/recall beats `evals/reports/baseline.json` on the same labeled set; with the judge call artificially delayed 30s the read path p95 is unchanged; a PII-bearing turn never reaches the provider client (asserted against a mock provider); queue at capacity returns `429` instead of failing the request path; every judge decision is replayable from its stored trace.
- **Loops:** L1, L2 (debug: provider fallback), L3 (research: provider limits), L4
- **Skills:** canon + tdd + llmops-ai-agents, security-engineering, distributed-systems
- **Token budget:** 50000

### M6 — Append-only audit log + "why do you know this?"
- **Outcome:** every admit / reject / update / supersede / archive / delete event is written append-only with actor, timestamp and source turn id, and `GET /v1/memories/{id}/audit` returns the full trail.
- **Phase:** Phase 15 Governance & Compliance
- **Files:** `app/audit/log.py`, `app/api/routes_audit.py`, `migrations/**`, `tests/acceptance/test_m6_audit.py`
- **Demo command:** `docker compose run --rm api pytest -q tests/acceptance/test_m6_audit.py`
- **Success criteria:** every mutation on a test memory produces exactly one corresponding audit row; an `UPDATE`/`DELETE` attempt against the audit table is rejected by the database; the audit trail for one memory explains its admission (source turn + judge rationale).
- **Loops:** L1, L4
- **Skills:** canon + tdd + security-engineering
- **Token budget:** 50000

### M7 — Hybrid retrieval + ranking service
- **Outcome:** `tsvector` keyword search and an entity-link graph signal join vector search, fused by a ranking service scoring recency × importance × relevance.
- **Phase:** Phase 6 Memory Architecture
- **Files:** `app/retrieval/keyword.py`, `app/retrieval/graph_links.py`, `app/retrieval/hybrid.py`, `app/ranking/scorer.py`, `migrations/**`, `evals/suites/hybrid/**`, `tests/acceptance/test_m7_hybrid.py`
- **Demo command:** `docker compose run --rm api sh -c "pytest -q tests/acceptance/test_m7_hybrid.py && python -m evals.run --suite hybrid --out evals/reports/hybrid.json --compare evals/reports/baseline.json"`
- **Success criteria:** precision@k improves over the vector-only baseline on an eval set built specifically from exact-name and relational queries that defeat vector-only search; ranking weights live in config, not code; the read path still meets its latency budget with all three signals on.
- **Loops:** L1, L3 (research: fusion/RRF weights), L4
- **Skills:** canon + tdd + data-systems-engineering, llmops-ai-agents
- **Token budget:** 50000

### M8 — Decay + reflection background jobs
- **Outcome:** a decay job reduces importance weights for unused memories and archives below threshold; a reflection agent consolidates related memories into higher-level summaries — both strictly off the request path.
- **Phase:** Phase 20 Continuous Learning (+ Phase 14 Data Engineering)
- **Files:** `worker/decay_job.py`, `worker/reflection_agent.py`, `worker/main.py`, `migrations/**`, `tests/acceptance/test_m8_decay.py`
- **Demo command:** `docker compose run --rm api pytest -q tests/acceptance/test_m8_decay.py`
- **Success criteria:** seeded memories aged 30/60/90 days without use show reduced ranking weight and archival at threshold with no manual intervention; reflection summaries are derived data linked to their sources; running either job twice produces the same end state (idempotent); request-path latency is unaffected while both jobs run.
- **Loops:** L1, L4
- **Skills:** canon + tdd + data-systems-engineering, llmops-ai-agents
- **Token budget:** 50000

### M9 — Human override, deletion, and conflict resolution
- **Outcome:** `PATCH` / `DELETE /v1/memories/{id}` are first-class (ownership + RLS enforced), and a contradicting fact marks the old record `superseded_by` the new one instead of duplicating it.
- **Phase:** Phase 19 Human-in-the-Loop Systems
- **Files:** `worker/conflict_resolver.py`, `app/api/routes_memories.py`, `app/domain/memory.py`, `tests/acceptance/test_m9_override.py`
- **Demo command:** `docker compose run --rm api pytest -q tests/acceptance/test_m9_override.py`
- **Success criteria:** a changed preference supersedes rather than duplicates and ranking returns only the live version; a hard-deleted memory and any summary derived solely from it are both gone while the deletion event remains in the audit log; a non-owner's `PATCH`/`DELETE` is refused.
- **Loops:** L1, L4
- **Skills:** canon + tdd + security-engineering, data-systems-engineering
- **Token budget:** 50000

### M10 — Observability, adversarial evaluation, and recovery runbook
- **Outcome:** the four core metrics (admission precision/recall, correction rate, retrieval precision@k, utilization) plus latency/cost/fallback-rate are traced per span; an adversarial suite covers prompt-injected "remember this" and procedural-memory poisoning; a `pg_dump`/WAL restore drill is scripted and documented.
- **Phase:** Phase 10 Observability & Tracing (+ Phase 9 Evaluation, Phase 12 Reliability)
- **Files:** `app/observability/**`, `evals/suites/adversarial/**`, `ops/restore_drill.sh`, `ops/RUNBOOK.md`, `tests/acceptance/test_m10_observability.py`
- **Demo command:** `docker compose run --rm api sh -c "pytest -q tests/acceptance/test_m10_observability.py && python -m evals.run --suite adversarial --out evals/reports/adversarial.json" && bash ops/restore_drill.sh`
- **Success criteria:** the adversarial suite runs clean (no false procedural admissions from assistant-authored or third-party content); every write/read span records latency + tokens + cost + provider-fallback flag; `ops/restore_drill.sh` restores into a scratch database and verifies row counts inside the documented time budget, printing PASS.
- **Loops:** L1, L2, L3, L4
- **Skills:** canon + tdd + production-readiness, llmops-ai-agents, security-engineering
- **Token budget:** 50000

> Out of scope for this plan (roadmap Phases 4–5 of the design doc, §15 / "Beyond Sprint 8"): dynamic
> agent routing with specialized extraction/evaluation/reflection/conflict agents, and cross-project
> Learning Memory. The architecture is shaped to add them without a rewrite; they are not in the v1 bar.

---

## Progress (loops append here on milestone completion — newest last)

- **M1 — Compose skeleton + health check · DONE 2026-07-30.** L1 BUILD (2 iters) → G4 computed green
  (demo 8/8 exit 0; ruff+mypy clean) → L4 VERIFY APPROVE (claude-opus-5-thinking-high, falsification
  probes passed) → quiz-me Q+A logged. Live: 4-service compose stack, real `GET /v1/health`
  (fail-open), heartbeat worker. 5 non-blocking follow-ups filed in `checkpoints/M1.md`.
