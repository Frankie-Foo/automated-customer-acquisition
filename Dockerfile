FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY templates ./templates
COPY migrations ./migrations
COPY config.example.yaml ./config.yaml

RUN pip install --no-cache-dir .

EXPOSE 8765

CMD ["salesbot-web", "--config", "config.yaml", "--host", "0.0.0.0", "--port", "8765"]
