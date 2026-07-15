#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

RELEASE_SHA="${1:-$(git rev-parse HEAD)}"
COMPOSE_FILE="${PROD_COMPOSE_FILE:-deployment/docker-compose.external-db.yml}"
ENV_FILE="${PROD_ENV_FILE:-deployment/production.env}"
IMAGE_REPOSITORY="${SALESBOT_IMAGE_REPOSITORY:-salesbot}"
IMAGE_TAG="${RELEASE_SHA:0:12}"
NEW_IMAGE="${IMAGE_REPOSITORY}:${IMAGE_TAG}"
STATE_FILE="deployment/.deployed-image"
PREVIOUS_IMAGE=""

if [[ ! "$RELEASE_SHA" =~ ^[0-9a-fA-F]{40}$ ]]; then
  echo "release SHA must be a full 40-character Git commit hash" >&2
  exit 1
fi

if [[ "$(git rev-parse HEAD)" != "$RELEASE_SHA" ]]; then
  echo "checked-out commit does not match requested release SHA" >&2
  exit 1
fi

if [[ -f "$STATE_FILE" ]]; then
  PREVIOUS_IMAGE="$(tr -d '\r\n' < "$STATE_FILE")"
fi

if [[ ! -f "$ENV_FILE" ]]; then
  echo "missing production env file: $ENV_FILE" >&2
  exit 1
fi

rollback() {
  trap - ERR
  if [[ -n "$PREVIOUS_IMAGE" ]] && docker image inspect "$PREVIOUS_IMAGE" >/dev/null 2>&1; then
    echo "deployment failed; rolling back to $PREVIOUS_IMAGE" >&2
    SALESBOT_IMAGE="$PREVIOUS_IMAGE" docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up -d --no-build
  else
    echo "deployment failed and no previous local image is available" >&2
  fi
}
trap rollback ERR

docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" config --quiet
docker build --pull \
  --label "org.opencontainers.image.revision=$RELEASE_SHA" \
  --label "org.opencontainers.image.source=$(git remote get-url origin 2>/dev/null || true)" \
  -t "$NEW_IMAGE" .

SALESBOT_IMAGE="$NEW_IMAGE" docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up -d --no-build

CONTAINER_ID="$(SALESBOT_IMAGE="$NEW_IMAGE" docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" ps -q salesbot)"
if [[ -z "$CONTAINER_ID" ]]; then
  echo "salesbot container was not created" >&2
  exit 1
fi

for _ in $(seq 1 60); do
  STATUS="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$CONTAINER_ID")"
  if [[ "$STATUS" == "healthy" ]]; then
    break
  fi
  if [[ "$STATUS" == "unhealthy" ]] || [[ "$STATUS" == "exited" ]] || [[ "$STATUS" == "dead" ]]; then
    docker logs --tail 200 "$CONTAINER_ID" >&2 || true
    exit 1
  fi
  sleep 3
done

STATUS="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$CONTAINER_ID")"
if [[ "$STATUS" != "healthy" ]]; then
  docker logs --tail 200 "$CONTAINER_ID" >&2 || true
  echo "salesbot did not become healthy" >&2
  exit 1
fi

PUBLIC_BASE_URL="$(sed -n 's/^PUBLIC_BASE_URL=//p' "$ENV_FILE" | tail -n 1 | tr -d '\r')"
if [[ -n "$PUBLIC_BASE_URL" ]]; then
  curl --fail --silent --show-error --retry 10 --retry-delay 3 "${PUBLIC_BASE_URL%/}/api/live" >/dev/null
fi

printf '%s\n' "$NEW_IMAGE" > "$STATE_FILE"
trap - ERR
echo "deployed $NEW_IMAGE successfully"
