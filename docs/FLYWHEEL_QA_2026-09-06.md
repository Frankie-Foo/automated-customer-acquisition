# Flywheel QA - 2026-09-06

## Release retest - 2026-09-07

- Full isolated PostgreSQL regression: 425 passed; frontend check/build and 2 PWA tests passed.
- Real three-account login acceptance passed again after fixing request/session consistency.
- A logout race could revoke a session between authorization and the follow-up query, causing HTTP 500.
  The handler now uses one identity snapshot per request and checks the session again on every new request.
  A deterministic regression checks successful in-flight access and subsequent 401 after revocation.
- Production configuration doctor passed before deployment; no production settings were changed.
- Docker/CI/release outcome will be recorded separately after the gates finish.

## Full application login acceptance - 2026-09-07

- Reproducible setup/test/serve runner: scripts/qa_client_acceptance.py.
- Fresh isolated PostgreSQL: all 49 migrations applied, repeat migrate returned [].
- Uses the real web handler, password/session login, bundled production frontend and sales_automation_runtime role.
- Three real local logins (two sales accounts, one admin), zero mocked requests.
- Sales sent counts 3 and 1; admin 4. Tampering with user_id in the query does not widen scope.
- Sales cannot read admin/users; logout makes email-performance return 401.
- 7/14/30-day API results and 30-day UI waiting state passed.
- Desktop/mobile screenshots and follow-up navigation passed; no page errors or HTTP 5xx.
- Actual PWA controller confirmed; reload retains the sent-mail view.
- Found and fixed a real integration race: the late session refresh reset the selected history tab.
  The sent view now retains #sent-emails, including reload, and its selection exposes aria-pressed.
- Full Python regression remains 424 passed; npm check includes 2 PWA tests and frontend build.
- Local preview: http://127.0.0.1:18773, sales login qa_sales / QA-local-only-2026.
  These credentials and fixture records exist only in the isolated salesbot_client_qa database.
- No .env loaded, provider keys, outbound mail, paid lookups, production deployment or production login.

Start the dedicated PostgreSQL container at local port 15447 with database salesbot_client_qa,
then run `python scripts/qa_client_acceptance.py setup`, `serve`, and `test` (serve in a separate process).
The serve mode starts no workers and performs no startup recovery.

## Sales client follow-up - 2026-09-07

- Full suite with isolated PostgreSQL: 424 passed. No production database was used.
- Added sales email-performance view under the existing sent-mail view, not an admin-only entry.
- API authenticated HTTP checks: anonymous 401; sales session scope retained; 7/14/30 accepted; invalid windows 400.
- Real PostgreSQL query checks: 163 sales messages retained (no 150-row list cap), other-owner exclusion,
  administrator totals, empty owner, duplicate replies, observation windows, simulated/future/old/non-email exclusions.
- Report is grouped by send week (Asia/Shanghai), scoped to current customer ownership, over the last 90 days.
  It is not a frozen sender-performance ledger or a causal experiment comparison.
- Browser fixture: 1440/390/320 widths, no horizontal overflow/page errors; observation switching,
  expand/collapse, load failure/retry, empty state and refresh events passed.
- Corrected fixture container to avoid mounting a standalone panel into the application's sidebar grid.
- PWA tests added to npm run check: pending-update notification, foreground check throttling,
  app-only cache cleanup, API/write bypass. Updates require user confirmation, never forced reload.
- PWA service-worker cache version changed and bundled client assets rebuilt.
- Screenshots: artifacts/flywheel-qa/performance-1440.png and performance-390.png (simulated data).
- Still not deployed or validated with a real production login. No customer mail or paid enrichment run.

## Verified locally

- Full suite with isolated PostgreSQL: 421 passed.
- PostgreSQL 16 temporary-table contract test uses the repository's actual experiment query.
- Duplicate replies count once per message. Unattributed and late replies do not earn experiment credit.
- Messages younger than 14 days are excluded from selection. Owner-scoped experiment queries tested.
- Direct In-Reply-To takes precedence over References. Ancestors identify the contact only.
- Browser fixture: actual AdminConsole component, simulated API responses, 1440 and 390 pixel widths.
- No browser page errors or horizontal overflow. Screenshots in artifacts/flywheel-qa/.
- Earlier full run encountered a Windows connection-aborted error in a web authorization test; subsequent full run passed.

## Not verified / not released

- No production database access, migration, email sends or paid enrichment calls.
- Real authenticated local acceptance passed on 2026-09-07 (see above); production login acceptance remains unverified.
- Existing localhost:8765 belongs to another project and was not changed.
- No claim of improved conversion or causal significance: outcome classification and business results need real audit.
- Legacy replies without exact attribution remain in customer history but do not select a winner.
- Experiments are not yet a complete per-batch 7/14/30-day reporting product.
- Aggregate experiment comparisons are not a post-change causal evaluation; multi-touch attribution and late outcomes remain future work.

## Reproduce

Set SALESBOT_QA_PG_DSN to an isolated PostgreSQL instance, then run:

```sh
python -m pytest -q
```

For fixture browser QA, start frontend Vite on 127.0.0.1:18771 and run:

```sh
python scripts/qa_flywheel_ui.py
```

The browser script mocks all API calls. It does not authenticate or modify real records.
