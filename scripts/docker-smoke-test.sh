#!/usr/bin/env sh
set -eu

PROJECT_NAME="${COMPOSE_PROJECT_NAME:-salesbot-ci}"
COMPOSE_FILE="${COMPOSE_FILE:-deployment/docker-compose.ci.yml}"

cleanup() {
  docker compose -p "$PROJECT_NAME" -f "$COMPOSE_FILE" down --volumes --remove-orphans >/dev/null 2>&1 || true
}

show_failure() {
  docker compose -p "$PROJECT_NAME" -f "$COMPOSE_FILE" ps || true
  docker compose -p "$PROJECT_NAME" -f "$COMPOSE_FILE" logs --no-color --tail=200 || true
}

trap cleanup EXIT
cleanup

docker compose -p "$PROJECT_NAME" -f "$COMPOSE_FILE" config --quiet
docker compose -p "$PROJECT_NAME" -f "$COMPOSE_FILE" build
docker compose -p "$PROJECT_NAME" -f "$COMPOSE_FILE" up -d

attempt=0
until curl --fail --silent --show-error http://127.0.0.1:18765/api/live >/dev/null; do
  attempt=$((attempt + 1))
  if [ "$attempt" -ge 60 ]; then
    show_failure
    echo "salesbot Docker smoke test timed out" >&2
    exit 1
  fi
  sleep 2
done

curl --fail --silent --show-error http://127.0.0.1:18765/ >/dev/null
curl --fail --silent --show-error http://127.0.0.1:18765/api/live
echo
echo "salesbot Docker smoke test passed"
