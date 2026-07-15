# LinkedIn Sales Automation

Self-hosted lead sourcing, enrichment, CRM status tracking, and cold email outreach for a small sales team.

## Quick Start

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
Copy-Item .env.example .env
Copy-Item config.example.yaml config.yaml
salesbot migrate --config config.yaml
salesbot-web --config config.yaml --host 127.0.0.1 --port 8765
```

Open `http://127.0.0.1:8765/`.

## Two Lead Paths

Path A: manual or CSV import.

Employees can add leads manually or upload a CSV. Supported columns include common names such as `linkedin_url`, `first_name`, `last_name`, `email`, `company`, `website`, `title`, `industry`, and `location`. The system deduplicates by `linkedin_url`; when a CSV has no LinkedIn URL, it creates a stable internal URL.

Path B: automated sourcing.

The app now uses a pluggable lead source model. `config.yaml` defaults to Prospeo:

```yaml
sourcing:
  provider: prospeo
```

The Dashboard `Lead Source` form accepts role, optional company website, industry, location, and limit. Prospeo is preferred for initial testing because it has a free plan with limited API access and monthly credits. NinjaPear remains available as an alternate provider when `NINJAPEAR_API_KEY` is configured.

Bulk Excel/CSV company imports run as durable background tasks. The upload request returns immediately; progress is stored in PostgreSQL and can be paused, resumed, or retried from the sourcing page. Completed tasks stop at customer review and never send automatically.

## Safe Email Workflow

Sales users open a private-pool customer, generate or edit a draft, then click `审核并锁定`. The backend only sends when the submitted subject and body exactly match the latest approved draft. Any edit requires approval again. Direct bulk-send endpoints are restricted to administrators.

## Common Commands

```powershell
salesbot source --config config.yaml --role "VP of Engineering" --company-website "stripe.com" --limit 25
salesbot enrich --config config.yaml --limit 100
salesbot queue --config config.yaml --limit 50
salesbot send --config config.yaml --limit 100
salesbot scheduler --config config.yaml
salesbot mark --config config.yaml --contact-id 1 --status replied
salesbot export --config config.yaml --status enriched --out exports/enriched.csv
salesbot blacklist --config config.yaml --email bad@example.com
```

## Production Config

本地 Docker 验收、GitHub CI、生产审批和自动回滚流程见 [docs/CI_CD.md](docs/CI_CD.md)。开发机发布前先运行：

```powershell
.\scripts\test-local-docker.ps1
```

`.env`:

```env
PROSPEO_API_KEY=
NINJAPEAR_API_KEY=
HUNTER_KEY=
RESEND_API_KEY=
DEEPSEEK_API_KEY=
RESEND_WEBHOOK_SECRET=
PUBLIC_BASE_URL=https://your-public-domain.example
TRACKING_SIGNING_SECRET=replace-with-at-least-32-random-characters
INBOUND_EMAIL_WEBHOOK_SECRET=replace-with-a-different-random-secret
```

`config.yaml`:

```yaml
llm:
  provider: deepseek
  base_url: https://api.deepseek.com
  model: deepseek-chat

sender:
  name: Your Name
  email: sales@your-verified-domain.com
  provider: resend
  daily_limit: 100
  dry_run: false
```

Resend webhook URL:

```text
https://your-public-domain.example/webhooks/resend
```

Localhost cannot receive Resend public webhooks. Deploy behind HTTPS, or use a temporary tunnel for testing. If `RESEND_WEBHOOK_SECRET` is set, the app verifies Resend/Svix signatures and deduplicates webhook deliveries with `svix-id`.

Corporate mailbox reply bridge:

```text
POST https://your-public-domain.example/webhooks/inbound-email
X-Inbound-Secret: <INBOUND_EMAIL_WEBHOOK_SECRET>
```

For per-salesperson sender aliases and automatic reply ownership, see [docs/CENTRALIZED_OUTBOUND_IDENTITY.md](docs/CENTRALIZED_OUTBOUND_IDENTITY.md). This mode keeps one verified sending/receiving infrastructure while routing each reply back to the correct private customer pool.

Connect Outlook/Power Automate, Microsoft Graph, or another inbound mailbox bridge using the payload documented in `docs/INBOUND_REPLY_BRIDGE.md`. A matched reply automatically records the email event, advances the contact to SABCD `B`, and creates a lifecycle reply activity.

## Outreach Flow

```text
name + website + title + country + industry -> LinkedIn identity evidence -> verified email -> current public research -> saved draft -> idempotent send -> delivered/opened/replied -> SABCD lifecycle
```

Default `sender.dry_run: true` prevents real email sends. Set it to `false` only after Resend domain verification and sender address setup are complete.

## Database

Migrations live in `migrations/`. Database credentials are read from `.env`; do not commit real secrets.

