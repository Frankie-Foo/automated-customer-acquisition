# Release Readiness - 2026-08-18

## Release scope

This release closes the unattended acquisition-to-sales-handoff path:

1. Scheduled country, industry, and role combinations create recoverable acquisition work items.
2. Public search and enrichment reuse evidence and caches, then run independent providers concurrently within configured budgets.
3. Authorized ContactOut jobs remain quota-controlled and separate from email sending.
4. Verified contacts enter the public or salesperson-owned pool with source evidence and deduplication.
5. Salespeople claim or receive customers, generate and approve personalized drafts, and send only from customers they own.
6. Open, delivery, bounce, unsubscribe, complaint, and reply events update messages without status regression.
7. Actionable replies stop old tasks, advance lifecycle, record interaction evidence, and create the next sales action.

## Verified release gates

- Python: `326 passed`
- Frontend: test-mode and production Vite builds pass
- Python package: bundled `web_static` is present; no `.env` is included
- PostgreSQL: 43 migrations applied; repeated migration returns `applied=[]`
- Docker: image builds; application and PostgreSQL containers are healthy
- Readiness: all required production checks pass with non-secret acceptance values
- Import: duplicate CSV retry leaves one contact, one lead, and one open task
- Permissions:
  - owner private detail: HTTP 200
  - other salesperson private detail: HTTP 404
  - administrator detail: HTTP 200
  - public pool detail: HTTP 200 for authenticated salespeople
  - private contact mutations by another salesperson: HTTP 403
- Outreach state: `sent -> opened -> delivered -> replied -> opened` remains `replied`, with all relevant timestamps retained
- Draft flow: public-pool claim, custom draft, quality review, approval, and follow-up creation pass against real PostgreSQL

## Production deployment gate

Before deploying, production operations must provide server-side secrets and run:

```bash
docker compose --env-file deployment/production.env \
  -f deployment/docker-compose.production.yml up -d --build

docker compose --env-file deployment/production.env \
  -f deployment/docker-compose.production.yml exec salesbot \
  salesbot --config /app/config.yaml doctor --strict
```

Do not deploy if `doctor.completed` reports `ready=false` or a required check is false.

Required operational integrations:

- PostgreSQL and backup retention
- at least one lead-source provider
- at least one valid-email enrichment provider
- verified SMTP/Resend transport and sender identity
- public HTTPS base URL and tracking signing secret
- IMAP or verified inbound reply webhook
- global and per-user source/send quotas
- strong administrator password

LLM, social enrichment, and Slack are optional. Deterministic fallbacks keep core processing available without LLM calls.

## Known non-code dependency

External providers determine actual lead yield, email validity, sending reputation, and reply delivery. Production credentials, DNS, mailbox access, webhook delivery, and provider balances must be verified in the production environment; repository tests cannot certify third-party account state.
