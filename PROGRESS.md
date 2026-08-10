# Progress

## 2026-08-10 — Backend data security P0

- API/MCP writes now require admin access or private-pool ownership; public-pool reads remain available to sales users.
- PostgreSQL 16 migration `036_tenant_owner_isolation.sql` adds restricted runtime role, transaction-local actor context, forced RLS, public-pool transition guard, and contact-child-table policies.
- All Python outbound HTTP paths use one URL gate that checks initial URLs, percent-decoded forms, and every redirect.
- Evidence: `pytest -q` → `282 passed in 5.78s`.
- Evidence: clean PostgreSQL `16-alpine` applied migrations `001`–`036`; live assertions passed for anonymous, sales, admin, own/private, other/private, public claim/return, owned sourcing enrichment, and child-row writes.
- Evidence: `git diff --check` passed.
