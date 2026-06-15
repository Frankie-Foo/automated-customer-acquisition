# Frontend Migration

The production UI is now built with React and Vite.

## Current Phase

This is an incremental migration:

- React renders the existing dashboard markup.
- The previous browser controller is loaded as a legacy controller after React mounts.
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

1. Extract API client and session/auth state into React modules.
2. Move admin console into React components.
3. Move contacts table and contact detail workspace.
4. Move sourcing/enrichment/send workflows.
5. Delete the legacy controller once all handlers are componentized.

