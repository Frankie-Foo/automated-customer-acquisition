FROM node:22-slim AS frontend-build

WORKDIR /app/frontend

COPY frontend/package*.json ./
RUN npm ci

COPY frontend ./
RUN npm run build

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY --from=frontend-build /app/frontend/dist ./src/sales_automation/web_static
COPY templates ./templates
COPY migrations ./migrations
COPY scripts ./scripts
COPY tools ./tools
COPY config.example.yaml ./config.yaml

RUN pip install --no-cache-dir .
RUN chmod +x /app/scripts/docker-entrypoint.sh

EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/api/live', timeout=3).read()"

ENTRYPOINT ["/app/scripts/docker-entrypoint.sh"]
CMD ["salesbot-web", "--config", "config.yaml", "--host", "0.0.0.0", "--port", "8765"]
