# Apollo phone fallback

Apollo is the final phone-only provider. It does not discover email addresses in this system.

## Processing order

1. Reuse existing valid contact data and cached provider results.
2. Run the regular email discovery providers independently.
3. Run the authorized ContactOut queue for missing personal email or phone data.
4. If no valid personal phone remains, enqueue Apollo phone enrichment.
5. Write verified personal phone candidates back to the same salesperson-owned contact.

Email discovery does not wait for phone discovery. Apollo cannot trigger email sending.

## Cost controls

- `APOLLO_PHONE_ENABLED=false` is the default.
- `APOLLO_GLOBAL_DAILY_CREDIT_LIMIT=0` is the default.
- Every salesperson has a separate `apollo_daily_credit_limit`, also defaulting to `0`.
- The worker reserves up to 9 credits before calling Apollo. If either limit lacks capacity, Apollo is not called.
- The signed Apollo webhook reports actual `credits_consumed`; unused reserved credits are released.
- Unknown provider outcomes are charged conservatively and blocked from automatic retry to prevent duplicate spend.
- A contact is eligible at most once per calendar month.

## Production setup

Set these only in `deployment/production.env`:

```dotenv
APOLLO_API_KEY=your-production-key
APOLLO_PHONE_ENABLED=true
APOLLO_GLOBAL_DAILY_CREDIT_LIMIT=90
APOLLO_WEBHOOK_SECRET=at-least-32-random-characters
APOLLO_PHONE_POLL_INTERVAL_SECONDS=60
```

Then use the administrator console to set each salesperson's daily Apollo credit limit. A value of `0` disables Apollo for that salesperson.

The request-specific callback is generated automatically under:

```text
https://your-public-domain/webhooks/apollo
```

No Apollo dashboard webhook registration is required for these phone requests.

## Operations

The production Docker Compose files run Apollo through the shared `scheduler-worker`. Administrators can inspect global and per-user used, reserved, and denied credits in the resource usage panel. Salespeople see only their own daily phone-credit usage.
