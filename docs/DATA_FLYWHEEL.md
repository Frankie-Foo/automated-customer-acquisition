# Data Flywheel

The outreach loop is now:

`send -> delivery/open/reply outcome -> reply classification/lifecycle result -> regional strategy -> next score and draft`

## What is automatic

- A scheduled run aggregates the latest 30 days of sent, delivered, opened, replied, bounced, lifecycle, won, and lost outcomes.
- Global and regional snapshots are stored in `flywheel_strategy_snapshots`.
- Active snapshots influence ICP scoring and AI draft guidance. They never turn an unverified email into a sendable email.
- The automatic learning pass derives labels only from strong outcomes: positive replies, meetings, wins, negative replies, bounces, unsubscribes, and losses.
- Neutral replies and opens are evidence for copy guidance, not ICP training labels.

## Automatic rule changes

### ICP threshold

- At least 10 high-signal labeled contacts are required.
- The threshold moves in steps of 5 points and is clamped to 40-90.
- At most one automatic threshold change is applied in a rolling 30-day window.
- Every change is written to `flywheel_learning_events` with before/after state and evidence.

### Email experiments

- Each variant needs at least 100 sends before a winner can be selected.
- The winner is chosen by positive-reply rate, not opens alone.
- The selected variant is saved as `outbound_experiments.winner_variant`.
- Future drafts use the winner; historical messages keep their original variant.

## Operations

- Scheduler: runs the loop after enrichment, queueing, sending, stale-pool recycling, and task refresh.
- Admin summary: `GET /api/flywheel`.
- Manual refresh: `POST /api/flywheel/run` with optional `window_days` and `min_samples`.
- Learning audit: returned as `learning_events` by the admin summary endpoint.

## Safety boundary

Automatic learning does not send email, rewrite customer-visible facts, bypass approval, or change ownership. It only changes bounded ICP/experiment strategy after decision-grade evidence is available.
