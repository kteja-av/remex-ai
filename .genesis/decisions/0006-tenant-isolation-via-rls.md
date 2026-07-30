# ADR 0006 — Tenant/User Isolation via Row-Level Security

- **Date:** 2026-07-30
- **Status:** accepted
- **Phase / milestone:** Phase 11 Security Architecture · M2
- **Source:** `../../../cmis-memory/remex_ai_system_design_v1.md` → "ADR 0006"

## Context
Cross-tenant memory leakage is the single highest-severity trust failure this system could have.

## Decision
Enforce tenant/user isolation with PostgreSQL row-level security (RLS) policies on every table holding
memory content, in addition to (not instead of) application-layer scoping.

## Consequences
- Positive: a query that forgets to scope by tenant still can't return another tenant's rows — the database
  itself is the last line of defense.
- Negative / cost: RLS policies add schema/migration overhead and are PostgreSQL-specific, coupling the
  design to this database choice (already accepted via ADR 0001).
- **Invariant added to context-graph.json:** `tenant_isolation_is_db_enforced`, checked by
  `pytest -q tests/invariants/test_rls_enforced.py`.

## Alternatives rejected
- Application-layer filtering only (every query manually scoped by `WHERE tenant_id = …`) — cheaper to
  implement, but a single missed filter in application code becomes a cross-tenant data leak with no
  backstop.
