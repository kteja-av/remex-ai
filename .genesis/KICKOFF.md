# KICKOFF — paste this to start or resume a remex-ai session cold

> Works in any agent. Replace the skill-invocation syntax per `AGENT-ADAPTERS.md`
> (Hermes `skill_view(name=…)` · Claude Code `Skill`/`/x` · Codex `$x`). The rest is identical.

```
Project: remex-ai — CMIS, a Conversational Memory Intelligence System.
Decide what a conversation is worth remembering, store it with provenance, and return the
smallest ranked set of memories that improves the next turn — without ever blocking the
conversation. Read path fails open; write path fails closed.
Stack: FastAPI + Postgres/pgvector + Redis(RQ) + local sentence-transformers embeddings,
self-hosted as one Docker Compose stack. Write Gate is async and queue-backed
(NVIDIA NIM / Gemini free tier, local-model fallback).
Design source of truth: ../cmis-memory/remex_ai_system_design_v1.md

Load skills (skill canon — always):
- agentic-swe-master          (orchestrator — routes everything, route before any code)
- modular-architecture, production-readiness
- per-milestone skills: see DONE.html section 4
- no frontend milestone in v1 (API-only), so the design-system skill does not apply

Read in order:
- AGENTS.md / CLAUDE.md                       (repo governance)
- .genesis/DONE.html                          (locked spec + definition of done + plan)
- .genesis/PLAN.md                            (milestones being executed)
- .genesis/context-graph.json                 (5 invariants — these are hard constraints)
- .genesis/wiki/index.md                      (then drill into pages matching the milestone's nouns)
- .genesis/implementation-notes.html          (search for the milestone's nouns — what's LIVE now)
- .genesis/LOOPS.md                           (how the work gets done)
- .genesis/checkpoints/CURRENT.md             (where we are, if it exists)

Then:
1. Pick the next unstarted milestone (or resume from CURRENT.md). Currently: M1.
2. Run G0 EXISTENCE PRE-FLIGHT first. Verdict UNBUILT → continue. PARTIAL → revise scope.
   BUILT → halt and surface the existing artifact.
3. Run L1 BUILD per LOOPS.md exactly. Enforce G0 + all 5 gates (G1 Skill, G2 Progress,
   G3 Cost, G4 Quality, G5 Verify). Gates are COMPUTED (run the command, paste exit code), not narrated.
   Quality commands for this repo:
     docker compose run --rm api sh -c "ruff check . && mypy app worker"
     docker compose run --rm api pytest -q
     docker compose run --rm api pytest -q tests/invariants
   Plus the milestone's own demo command from PLAN.md — it must exit 0.
4. Checkpoint every iteration to .genesis/checkpoints/<milestone-id>.md.
5. Spawn L2 DEBUG / L3 RESEARCH as needed. Exit through L4 VERIFY (separate model, fresh context:
   claude-opus-5-thinking-high).
6. On milestone done: update CURRENT.md, append a row to implementation-notes.html "what's live",
   append progress to PLAN.md.

Hard constraints (from context-graph.json — a change that breaks one of these is not done):
- read path fails open (200 + degraded:true, never 5xx)
- read path makes no external network calls (local embeddings/keyword/graph only)
- no durable write without provenance, and the PII verdict precedes any external LLM call
- tenant isolation is DB-enforced via RLS, on every memory-bearing table
- background jobs never block the request path; app/api never imports worker internals

Stop rules: if any gate fails 3 times, stop, write what you tried to CURRENT.md, surface to the user.
Never mark a milestone done without L4 VERIFY APPROVE. Never edit DONE.html / PLAN.md without being asked.
```
