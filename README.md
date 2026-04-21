# Kafka Event Enrichment Pipeline

Production-style reference implementation: **Kafka** for transport, **Redis** for idempotency + a **local user projection** hydrated from `user-updates`, **FastAPI/asyncio** services, **retry** + **DLQ**, **Prometheus** metrics, optional **Streamlit** dashboard, and **Docker Compose**.

## Architecture

1. **Producer** → `raw-events`
2. **User Service** → publishes full user snapshots to `user-updates` (and serves rare `GET /user/{id}` fallback)
3. **Stream processor** consumes `raw-events` + `user-updates`, updates Redis, enriches, writes `enriched-events` or `retry-events`
4. **Retry worker** consumes `retry-events`, exponential backoff, max attempts → `dead-letter-queue`
5. **DLQ handler** persists DLQ records (JSONL) and increments Redis stats

**Horizontal scaling:** Run multiple instances of `stream-processor` and `retry-worker` with the same consumer group id (already set in code). For Compose, omit fixed host ports when scaling (or use one replica locally).

## Prerequisites

- Docker with Compose v2
- Ports free: 2181, 6379, 8080, 8002, 8004, 8005, 8501, 9092, 9094

## Run locally

From the repository root:

```bash
docker compose -f infra/docker-compose.yml up --build
```

Services:

| Service           | Port (host) | Role                                      |
|------------------|-------------|-------------------------------------------|
| Kafka (internal) | 9092        | In-docker bootstrap                       |
| Kafka (external) | 9094        | Host tools / load sim                     |
| Redis            | 6379        | Idempotency + user projection             |
| User Service     | 8080        | `GET /user/{id}`, admin demo endpoints    |
| Stream processor | 8002        | `/metrics`, `/health`                     |
| Retry worker     | 8004        | `/metrics`, `/health`                     |
| DLQ handler      | 8005        | `/metrics`, `/health`                     |
| Dashboard        | 8501        | Streamlit                                 |

## Demo scenarios

With the stack running:

```bash
chmod +x scripts/demo.sh
USER_SERVICE_URL=http://localhost:8080 ./scripts/demo.sh
```

1. **Normal flow** — producer emits events; user data is served from Redis after `user-updates` replication.
2. **Duplicates** — producer periodically reuses the same `eventId`; duplicates are skipped via Redis idempotency.
3. **User Service “down” for a user** — `POST /admin/simulate-down` removes user `123` from memory so HTTP fallback fails; events for that user move through `retry-events` and eventually **DLQ** if still unresolved.
4. **Restore** — `POST /admin/restore-user-123` republishes the user to `user-updates` so enrichment succeeds again.

## Load simulation

Install deps locally (or run in a throwaway container with the same image as producer):

```bash
export PYTHONPATH=.
export KAFKA_BOOTSTRAP_SERVERS=localhost:9094
export LOAD_RATE_PER_SEC=500
export LOAD_DURATION_SEC=15
python scripts/load_sim.py
```

## Observability

- Prometheus text on each service’s `/metrics`.
- Streamlit dashboard at `http://localhost:8501` polls those endpoints.

## Project layout

```
infra/docker-compose.yml
shared/                  # Schemas, topics, enrichment helpers
services/
  producer/
  consumer/              # Stream processor (core)
  user_service/
  retry_worker/
  dlq_handler/
dashboard/               # Streamlit
scripts/                 # demo.sh, load_sim.py
```

## Design notes

- **No per-event User Service calls in the steady state:** enrichment reads Redis populated from `user-updates`. HTTP is only used when the projection misses a user.
- **Idempotency:** Redis key `idem:{eventId}` set to `enriched` or `dlq` on terminal outcomes; duplicates are skipped.
- **Offsets:** Kafka offsets committed only after successful handling of each message (manual commits).
- **Retries:** Raw path emits `retry-events` with `attempt=1`; retry worker increments up to `MAX_RETRY_ATTEMPTS` (default 3) with exponential backoff, then DLQ.
