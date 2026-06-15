# Frontend Migration

The production UI is now built with React and Vite.

## Current Phase

This is an incremental migration:

- React renders the existing dashboard markup.
- Login/session and the admin console are now React components.
- Login/session, the admin console, contacts pipeline, contact detail workspace, and sourcing workbench are now React components.
- The previous browser controller is still loaded for operations report, lifecycle summary, readiness, and remaining support workflows.
- Existing backend APIs, sessions, permissions, and workflows remain unchanged.
- Future work should move one workflow at a time from `legacy-controller.js` into typed React components.

This avoids breaking login, sourcing, enrichment, sending, lifecycle, and admin operations during the migration.

## Local Development

Install dependencies:

```bash
cd frontend
npm install
```

Run Vite for frontend-only development:

```bash
npm run dev
```

Build and copy static assets into the Python package:

```bash
npm run build:python-static
```

Then run the existing Python web server:

```bash
salesbot-web --config config.yaml --host 127.0.0.1 --port 8765
```

## Production Build

Docker builds the React app in a Node stage, then copies `frontend/dist` into `src/sales_automation/web_static` before installing the Python package.

Use the existing deployment command:

```bash
docker compose up -d --build
```

## Next Refactor Order

1. Move operations report and lifecycle views.
2. Move readiness/production status panel.
3. Move remaining support workflows and remove hidden legacy markup.
4. Delete the legacy controller once all handlers are componentized.
