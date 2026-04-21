# Kafka Event Enrichment Pipeline

Production-style reference: **Kafka**, **Redis** projection + idempotency, **PostgreSQL** for users and enriched results, **Confluent Schema Registry** + **Avro** payloads, **Kafka transactions (EOS)** for produce + offset commit, **consumer lag** metrics, **OpenTelemetry** → **Jaeger**, **circuit breaker** on User Service HTTP, **non-blocking retry** (`retry_after`), **DLQ replay** API, and **Streamlit** dashboard.

## Architecture

1. **Producer** → `raw-events` (W3C trace context in headers when OTLP is configured)
2. **User Service** → Postgres + publishes snapshots to `user-updates`
3. **Stream processor** → `raw-events` + `user-updates` → Redis → `enriched-events` or `retry-events` (transactional EOS optional)
4. **Retry worker** → `retry-events` → backoff via `retry_after` republish → `enriched-events` or DLQ
5. **Result service** → consumes `enriched-events` with **`isolation_level=read_committed`** → Postgres `enriched_results`
6. **DLQ handler** → persists DLQ JSONL, **`POST /replay`** back to `raw-events`

**Horizontal scaling:** use a **unique** `KAFKA_TRANSACTIONAL_ID` / hostname per `stream-processor` replica. For Compose, `docker compose up --scale stream-processor=2` requires removing conflicting host port mappings for that service.

## Prerequisites

- Docker Compose v2 (`docker compose`, not `docker -f`)
- Ports: 4317, 5432, 6379, 8080, 8081, 8002, 8004, 8005, 8006, 8501, 9092, 9094, 16686 (Jaeger UI)

## Run locally

```bash
docker compose -f infra/docker-compose.yml up --build
```

| Service           | Port | Notes |
|-------------------|------|--------|
| Postgres          | 5432 | DB `pipeline`, user `pipeline` / `pipeline` |
| Schema Registry   | 8081 | Avro subjects |
| Jaeger UI         | 16686 | Traces (OTLP gRPC 4317) |
| Redis             | 6379 | |
| User Service      | 8080 | |
| Stream processor  | 8002 | Lag + circuit metrics |
| Retry worker      | 8004 | |
| DLQ handler       | 8005 | `POST /replay` |
| Result service    | 8006 | `GET /results`, `/results/stats` |
| Dashboard         | 8501 | Lag panels, DLQ replay, result table |

### Environment highlights

| Variable | Purpose |
|----------|---------|
| `KAFKA_ENABLE_TXN` | `1` = transactional producer + `send_offsets_to_transaction` (default in Compose) |
| `KAFKA_TRANSACTIONAL_ID` | Unique per process; default `stream-processor-txn-{hostname}` |
| `KAFKA_USE_AVRO` | `1` = Confluent wire format + Schema Registry |
| `SCHEMA_REGISTRY_URL` | e.g. `http://schema-registry:8081` |
| `DATABASE_URL` | Async SQLAlchemy + asyncpg |
| `OTLP_ENDPOINT` | e.g. `jaeger:4317` — if unset, tracing is a no-op |

## Demo scenarios

```bash
chmod +x scripts/demo.sh
USER_SERVICE_URL=http://localhost:8080 ./scripts/demo.sh
```

1. **Normal flow** — events land in **Result service** (`/results`) and the dashboard table.
2. **Duplicates** — same `eventId` skipped via Redis idempotency.
3. **Failure** — `POST /admin/simulate-down` deletes user `123` from Postgres; unresolved events → retry → DLQ.
4. **Replay** — Dashboard **“Replay DLQ”** or `curl -X POST http://localhost:8005/replay?limit=100`.
5. **Traces** — Open [Jaeger UI](http://localhost:16686) and search by service (`producer`, `stream-processor`, …).

## Load simulation

```bash
export PYTHONPATH=.
export KAFKA_BOOTSTRAP_SERVERS=localhost:9094
python scripts/load_sim.py
```

## Project layout

```
infra/docker-compose.yml
shared/                  # Schemas, Avro, serde, tracing, lag helpers, enrichment + CB
services/
  producer/, consumer/, user_service/, retry_worker/, dlq_handler/, result_service/
dashboard/
scripts/
```

## Design notes

- **EOS:** Outbound writes + consumer offset for `raw-events` / `retry-events` share a Kafka transaction when `KAFKA_ENABLE_TXN=1`. Redis idempotency keys are set **after** a successful transaction commit.
- **Result consumer** uses `read_committed` so it does not read aborted transactional messages.
- **Retry:** `retry_after` schedules republish without blocking the consumer loop for the full backoff duration.
- **Circuit breaker:** `user_service_circuit_breaker_open` gauge in Prometheus metrics (from shared enrichment module).
