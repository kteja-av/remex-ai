# ADR 0004 — Self-Hosted Deployment via Docker Compose

- **Date:** 2026-07-30
- **Status:** accepted
- **Phase / milestone:** Phase 13 Infrastructure & Deployment · M1
- **Source:** `../../../cmis-memory/remex_ai_system_design_v1.md` → "ADR 0004"

## Context
Project constraint: self-host on a VPS rather than a managed cloud, using open-source/free-tier model
providers.

## Decision
Deploy as a single Docker Compose stack: Postgres+pgvector, FastAPI service, Redis (queue), Write
Gate/Reflection worker, reverse proxy (Caddy). No managed cloud services.

## Consequences
- Positive: full cost control; matches the free/open-source model constraint.
- Negative / cost: no managed autoscaling — capacity planning is manual (vertical scaling first, then
  Postgres read replicas, then additional worker nodes).
- Negative / cost: no managed database failover — requires an explicit backup/restore runbook
  (`pg_dump` + WAL archiving) as a v1 deliverable, not future work (M10).
- **Invariant implication:** all quality gates run inside the stack
  (`docker compose run --rm api …`), so nothing is installed on the host.

## Alternatives rejected
- A specific managed cloud (AWS/GCP/Azure) — would provide autoscaling and managed Postgres failover, but
  contradicts the self-hosting constraint and adds cost.
