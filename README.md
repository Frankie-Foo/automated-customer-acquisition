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

`.env`:

```env
PROSPEO_API_KEY=
NINJAPEAR_API_KEY=
HUNTER_KEY=
RESEND_API_KEY=
DEEPSEEK_API_KEY=
RESEND_WEBHOOK_SECRET=
PUBLIC_BASE_URL=https://your-public-domain.example
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

## Outreach Flow

```text
manual/CSV/API leads -> contacts table -> enrichment -> queued -> sent_1 -> sent_2 -> sent_3 -> replied/bounced/unsubscribed
```

Default `sender.dry_run: true` prevents real email sends. Set it to `false` only after Resend domain verification and sender address setup are complete.

## Database

Migrations live in `migrations/`. Database credentials are read from `.env`; do not commit real secrets.

