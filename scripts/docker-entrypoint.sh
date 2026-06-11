#!/bin/sh
set -eu

CONFIG_PATH="${SALESBOT_CONFIG:-config.yaml}"
WAIT_TIMEOUT="${SALESBOT_DB_WAIT_TIMEOUT:-90}"
STARTED_AT="$(date +%s)"

echo "salesbot: waiting for PostgreSQL"
while ! salesbot --config "$CONFIG_PATH" doctor --database-only >/tmp/salesbot_doctor.log 2>&1; do
  NOW="$(date +%s)"
  if [ $((NOW - STARTED_AT)) -ge "$WAIT_TIMEOUT" ]; then
    cat /tmp/salesbot_doctor.log || true
    echo "salesbot: database did not become ready within ${WAIT_TIMEOUT}s" >&2
    exit 1
  fi
  sleep 3
done

echo "salesbot: running migrations"
salesbot --config "$CONFIG_PATH" migrate

if [ "${SALESBOT_REQUIRE_PRODUCTION_READY:-false}" = "true" ]; then
  echo "salesbot: running strict production readiness check"
  salesbot --config "$CONFIG_PATH" doctor --strict
fi

exec "$@"
