# Production Update 2026-07-12

This update adds migrations `024_automation_runs.sql`, `025_email_draft_approval.sql`, and `026_app_settings.sql`.

## Deploy

```bash
cd /opt/salesbot
git pull
docker compose build --no-cache app
docker compose run --rm app salesbot migrate --config config.yaml
docker compose up -d
```

If the Compose service is not named `app`, replace `app` with the actual application service name.

## Verify

```bash
curl -fsS https://global-autoleads.vertu.cn/api/live
curl -fsS https://global-autoleads.vertu.cn/api/health
docker compose logs --tail=200 app
```

Then verify in the browser:

1. Sales login only shows Workbench, Sourcing, Customer Verification, Email Outreach, and Follow-up.
2. Uploading a company file creates a background task and returns immediately.
3. The task shows progress and can be paused or retried.
4. A sales user can create a contact without a LinkedIn URL.
5. The email send button is disabled until the draft is approved.
6. Editing an approved draft disables sending until it is approved again.
7. Resend/inbound webhooks still update delivered, opened, replied, bounced, and lifecycle feedback.
8. The administrator can save region assignment rules and new background sourcing runs apply them only to contacts created by that run.
9. A completed sourcing run creates fallback customer profiles and prepares a limited number of private-pool email drafts, but never sends automatically.
10. Narrow sales screens show one navigation bar, ten customer rows per page, and reset to the top after each page change.
11. Email outreach opens on the customer workspace; send history loads only after selecting “发送记录与回流”.
12. Sales users can filter their private pool by missing draft, pending approval, and approved-to-send without seeing another owner’s drafts.

## Rollback Note

The migrations are additive. Rolling back the application image does not require dropping the new tables or columns. Do not reverse PostgreSQL migrations in production unless a restore has been tested.
