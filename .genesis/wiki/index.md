# Wiki Index — remex-ai

The project knowledge base. Same schema as the agentic-swe-kit wiki: concept pages in `concepts/`,
each with frontmatter and ≥2 `[[wikilinks]]`. The L3 RESEARCH loop writes here; G0 reads here first.

> **Read this file before any milestone (G0 step 1).** Pick candidate pages by name-matching the
> milestone's nouns, then drill in. The wiki is what prevents rebuilding work that already exists.

Project: CMIS — Conversational Memory Intelligence System.
Source of truth for the design: `../../../cmis-memory/remex_ai_system_design_v1.md`
(system design + sprint plan + threat model + API contracts + ADRs 0001–0006, all in that one file).

## Entities (the things this system has)
<!-- Stubs to write as they are built — one page per noun the loops will keep touching. -->
- [[concepts/Memory-Record]] — typed row (episodic / semantic / procedural) + provenance + decay weight. _stub_
- [[concepts/Write-Gate]] — async queue-backed LLM judge: admit / reject / update-existing. _stub_
- [[concepts/Admission-Queue]] — Redis+RQ job queue, bounded, `429` backpressure. _stub_
- [[concepts/Hybrid-Retrieval]] — vector + `tsvector` keyword + entity-link graph, fused by ranking. _stub_
- [[concepts/Ranking-Service]] — recency × importance × relevance (+ reflection summaries). _stub_
- [[concepts/Context-Block]] — token-budgeted, cited memory block; rank-aware placement. _stub_
- [[concepts/PII-Filter]] — pre-send and pre-store gate on the untrusted egress boundary. _stub_
- [[concepts/Audit-Log]] — append-only decision trail answering "why do you know this?". _stub_
- [[concepts/Decay-Job]] — background weight reduction + archival. _stub_
- [[concepts/Reflection-Agent]] — periodic consolidation into higher-level summaries. _stub_

## Concepts (how it works)
<!-- Project-specific mechanics — fill as decided, link back to the ADR that fixed them. -->
- [[concepts/Fail-Open-Read-Path]] — retrieval degrades to empty, never blocks a response (invariant #1). _stub_
- [[concepts/Fail-Closed-Write-Path]] — no partial writes; PII rejection kills the whole candidate. _stub_
- [[concepts/Supersede-Not-Duplicate]] — conflict resolution marks `superseded_by` at write time. _stub_
- [[concepts/Procedural-Memory-Poisoning]] — memory store as an injection persistence vector. _stub_
- [[concepts/Provider-Fallback-Routing]] — NIM / Gemini free tier → local model when rate-limited. _stub_

## Sources (research distilled by L3)
- [[concepts/lost-in-the-middle]] — Liu et al.: mid-context blindness ⇒ placement matters as much as selection. _stub_
- [[concepts/unable-to-forget]] — Wang et al.: proactive interference ⇒ models can't self-unbind stale facts. _stub_

## Seeded from agentic-swe-kit
Relevant global concept pages for this project's phases (pointers only — read on demand).
Root: `$AGENTIC_SWE_WIKI_ROOT` = `~/.agentic-swe-kit/wiki`

**Phase 0/1 — cognitive design + architecture boundaries**
- `$AGENTIC_SWE_WIKI_ROOT/clean-architecture/concepts/Boundary-Lines.md` — where to cut app/ vs worker/
- `$AGENTIC_SWE_WIKI_ROOT/clean-architecture/concepts/Dependency-Rule.md` — enforcing the inward direction in `context-graph.json`
- `$AGENTIC_SWE_WIKI_ROOT/clean-architecture/concepts/Database-as-Detail-The-database-is-a-low-level-mechanism-like-a-doorknob-that-do.md` — before coupling domain logic to pgvector
- `$AGENTIC_SWE_WIKI_ROOT/distributed-systems/concepts/System-Architecture-Styles.md` — API + worker + queue topology

**Phase 4/5 — orchestration + LLM layer (Write Gate, reflection)**
- `$AGENTIC_SWE_WIKI_ROOT/llmops-ai-agents/concepts/Orchestrator-Worker-Architecture.md` — the V1 single-orchestrator shape (§6)
- `$AGENTIC_SWE_WIKI_ROOT/llmops-ai-agents/concepts/Agentic-Design-Patterns.md` — when to split into specialized agents (V2/V3)
- `$AGENTIC_SWE_WIKI_ROOT/llmops-ai-agents/concepts/LLMOps-Essentials.md` — prompt versioning, structured output validation
- `$AGENTIC_SWE_WIKI_ROOT/llmops-ai-agents/concepts/Metacognitive-Agents.md` — reflection vs learning separation

**Phase 6 — memory architecture + retrieval (the core of this project)**
- `$AGENTIC_SWE_WIKI_ROOT/llmops-ai-agents/concepts/RAG-Architecture.md` — hybrid retrieval, reranking, citation grounding
- `$AGENTIC_SWE_WIKI_ROOT/designing-data-intensive-applications/concepts/Storage-Engines.md` — pgvector index behaviour and cost
- `$AGENTIC_SWE_WIKI_ROOT/designing-data-intensive-applications/concepts/Conflict-Resolution.md` — supersede semantics for contradicting facts
- `$AGENTIC_SWE_WIKI_ROOT/designing-data-intensive-applications/concepts/Encoding-and-Schema-Evolution.md` — memory record schema migrations
- `$AGENTIC_SWE_WIKI_ROOT/designing-data-intensive-applications/concepts/Transactions-and-Isolation.md` — write path atomicity

**Phase 9/10 — evaluation + observability**
- `$AGENTIC_SWE_WIKI_ROOT/llmops-ai-agents/concepts/Evaluation-Frameworks.md` — golden sets, LLM-as-judge calibration
- `$AGENTIC_SWE_WIKI_ROOT/llmops-ai-agents/concepts/Observability-and-Cost-Control.md` — span-level latency/token/cost
- `$AGENTIC_SWE_WIKI_ROOT/release-it/concepts/Transparency-and-Observability.md` — the four core metrics + alert thresholds

**Phase 11 — security + threat model**
- `$AGENTIC_SWE_WIKI_ROOT/security-engineering/concepts/Threat-Modeling.md` — STRIDE pass, adversary categories
- `$AGENTIC_SWE_WIKI_ROOT/security-engineering/concepts/Access-Control.md` — RLS + the three roles (user / host app / operator)
- `$AGENTIC_SWE_WIKI_ROOT/security-engineering/concepts/Privacy-and-Inference-Control.md` — PII filtering, residency, inference leakage
- `$AGENTIC_SWE_WIKI_ROOT/security-engineering/concepts/Multilevel-Security.md` — tenant isolation reasoning

**Phase 12/13 — reliability + infrastructure**
- `$AGENTIC_SWE_WIKI_ROOT/release-it/concepts/Timeouts.md` — every outbound call in `worker/**`
- `$AGENTIC_SWE_WIKI_ROOT/release-it/concepts/Circuit-Breaker.md` — provider outage / rate-limit exhaustion
- `$AGENTIC_SWE_WIKI_ROOT/release-it/concepts/Bulkheads.md` — API vs worker isolation
- `$AGENTIC_SWE_WIKI_ROOT/release-it/concepts/Steady-State.md` — decay/archival so the store doesn't grow unbounded
- `$AGENTIC_SWE_WIKI_ROOT/release-it/concepts/Recovery-Patterns.md` — pg_dump / WAL restore runbook (M10)
- `$AGENTIC_SWE_WIKI_ROOT/release-it/concepts/Capacity-Framework.md` — manual VPS capacity planning (no autoscaling)

**Phase 14/15 — data pipelines + governance**
- `$AGENTIC_SWE_WIKI_ROOT/designing-data-intensive-applications/concepts/Batch-Processing-Patterns.md` — idempotent decay/reflection jobs
- `$AGENTIC_SWE_WIKI_ROOT/designing-data-intensive-applications/concepts/Derived-Data-Systems.md` — reflection summaries as derived data
- `$AGENTIC_SWE_WIKI_ROOT/security-engineering/concepts/Secure-Development-and-Assurance.md` — audit trail assurance
