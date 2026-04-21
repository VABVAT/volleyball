#!/usr/bin/env bash
# Demo script: exercise normal flow, duplicates, failure simulation, retry, DLQ.
set -euo pipefail

BASE_USER="${USER_SERVICE_URL:-http://localhost:8080}"

echo "== Health checks =="
curl -sf "${BASE_USER}/health" | jq .
curl -sf "http://localhost:8002/health" | jq .
curl -sf "http://localhost:8004/health" | jq .
curl -sf "http://localhost:8005/health" | jq .

echo
echo "== Simulate User Service missing user 123 (API + local projection stale after manual demo) =="
curl -sf -X POST "${BASE_USER}/admin/simulate-down" | jq .

echo
echo "Wait for pipeline to route failures to retry/DLQ (adjust sleep if needed)..."
sleep 8

echo
echo "== Restore user 123 and publish to user-updates =="
curl -sf -X POST "${BASE_USER}/admin/restore-user-123" | jq .

echo
echo "== Sample metrics (stream processor) =="
curl -sf "http://localhost:8002/metrics" | head -n 40

echo
echo "Done. Open Streamlit at http://localhost:8501 if the dashboard container is running."
