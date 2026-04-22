#!/usr/bin/env bash
# Stop pipeline consumers, delete pipeline Kafka topics, recreate them empty, restart services.
# Use this to clear topic backlogs for a fresh run without removing Postgres.
#
# Usage:
#   ./scripts/reset-kafka-fresh.sh           # reset Kafka topics only
#   ./scripts/reset-kafka-fresh.sh --redis # also FLUSHALL on Redis (clears idempotency / cache keys)
#
# Requires: Docker Compose stack from repo root (infra/docker-compose.yml), Kafka broker up.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

COMPOSE=(docker compose -f infra/docker-compose.yml)
FLUSH_REDIS=false
for arg in "$@"; do
  case "$arg" in
    --redis) FLUSH_REDIS=true ;;
    -h | --help)
      echo "Usage: $0 [--redis]"
      echo "  --redis  Run redis-cli FLUSHALL after topic reset (clears stream-processor idempotency keys)."
      exit 0
      ;;
  esac
done

# Everything that should release consumer connections / producers before topic delete.
KAFKA_CLIENTS=(
  dashboard-ui
  dashboard-api
  result-service
  dlq-handler
  retry-worker
  stream-processor
  producer
  user-service
  schema-registry
)

echo "== Stopping Kafka clients (keeps kafka, postgres, redis, jaeger running) =="
"${COMPOSE[@]}" stop "${KAFKA_CLIENTS[@]}"

echo "== Deleting pipeline topics =="
"${COMPOSE[@]}" exec -T kafka bash -c '
set -e
BOOT="localhost:9092"
for t in raw-events user-updates enriched-events retry-events dead-letter-queue; do
  if /opt/kafka/bin/kafka-topics.sh --bootstrap-server "$BOOT" --list 2>/dev/null | grep -qx "$t"; then
    echo "deleting topic $t"
    /opt/kafka/bin/kafka-topics.sh --bootstrap-server "$BOOT" --delete --topic "$t"
  else
    echo "topic $t not present, skip"
  fi
done
echo "waiting for broker to finish deletes..."
sleep 5
echo "remaining topics:"
/opt/kafka/bin/kafka-topics.sh --bootstrap-server "$BOOT" --list | grep -E "^(raw-events|user-updates|enriched-events|retry-events|dead-letter-queue)$" || true
'

echo "== Recreating topics (kafka-init) =="
"${COMPOSE[@]}" run --rm --no-deps kafka-init

if [[ "$FLUSH_REDIS" == true ]]; then
  echo "== Redis FLUSHALL =="
  "${COMPOSE[@]}" exec -T redis redis-cli FLUSHALL
fi

echo "== Starting full stack =="
"${COMPOSE[@]}" up -d

echo "Done. Pipeline topics are empty; restart metrics counters by process recycle (compose up -d)."
