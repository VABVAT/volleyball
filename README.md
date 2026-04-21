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

- **All-in-Docker:** Docker with Compose v2 (command is `docker compose`, not `docker -f`; see [Docker note](#docker-compose-cli) below)
- **Native apps:** Python 3.12+, a running **Kafka** broker, **Redis**, and open ports (see below)

## Run locally (Docker Compose)

From the repository root:

```bash
docker compose -f infra/docker-compose.yml up --build
```

### Docker Compose CLI

If you see `unknown shorthand flag: 'f' in -f`, you likely ran `docker -f ...` by mistake. The Compose file flag belongs to the **`compose`** subcommand:

```bash
docker compose -f infra/docker-compose.yml up --build
```

If your install only has the legacy binary, use:

```bash
docker-compose -f infra/docker-compose.yml up --build
```

## Run without Docker (native processes)

You still need **Kafka** and **Redis** running on your machine (install via Homebrew, tarballs, or another package manager). This repo assumes a single Kafka bootstrap like **`localhost:9092`** for local tools.

### 1. Install Python dependencies

```bash
cd /path/to/volleyball
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
export PYTHONPATH="$PWD"    # repo root on PYTHONPATH
```

### 2. Start Redis

Example (Homebrew):

```bash
brew install redis
brew services start redis
# or: redis-server
```

Default URL used below: `redis://127.0.0.1:6379/0`.

### 3. Start Kafka and create topics

Use your install’s `kafka-topics.sh` (path varies). Create the same topics as Compose (3 partitions, RF 1 is fine for local dev):

```bash
export KAFKA_BOOTSTRAP=localhost:9092   # adjust to your broker

for topic in raw-events user-updates enriched-events retry-events dead-letter-queue; do
  kafka-topics.sh --create --if-not-exists \
    --bootstrap-server "$KAFKA_BOOTSTRAP" \
    --topic "$topic" --partitions 3 --replication-factor 1
done
```

If auto-creation is enabled and you accept defaults, you can skip this step, but explicit topics avoid surprises.

### 4. Environment (all services)

In **each** terminal, set the repo root on `PYTHONPATH` and point everything at localhost:

```bash
export PYTHONPATH="/path/to/volleyball"
export KAFKA_BOOTSTRAP_SERVERS=localhost:9092
export REDIS_URL=redis://127.0.0.1:6379/0
export USER_SERVICE_URL=http://127.0.0.1:8080
```

Optional: `export DLQ_STORAGE_PATH=/tmp/dlq_store.jsonl`

### 5. Start services (order matters)

Use **separate terminals** from the repo root (with venv activated and `PYTHONPATH` set).

1. **User service** (must be up before the stream processor so HTTP fallback works; it also seeds `user-updates` on startup):

   ```bash
   python -m uvicorn services.user_service.main:app --host 0.0.0.0 --port 8080
   ```

2. **Stream processor** (consumer):

   ```bash
   export METRICS_PORT=8002
   python -m services.consumer.main
   ```

3. **Retry worker:**

   ```bash
   export METRICS_PORT=8004
   python -m services.retry_worker.main
   ```

4. **DLQ handler:**

   ```bash
   export METRICS_PORT=8005
   python -m services.dlq_handler.main
   ```

5. **Producer** (mock traffic):

   ```bash
   python -m services.producer.main
   ```

6. **Dashboard** (optional):

   ```bash
   export METRICS_STREAM_PROCESSOR=http://127.0.0.1:8002
   export METRICS_RETRY_WORKER=http://127.0.0.1:8004
   export METRICS_DLQ_HANDLER=http://127.0.0.1:8005
   export METRICS_USER_SERVICE=http://127.0.0.1:8080
   streamlit run dashboard/app.py --server.port 8501
   ```

### 6. Verify

- User service: `curl -s http://127.0.0.1:8080/health`
- Stream processor: `curl -s http://127.0.0.1:8002/metrics | head`

Use `scripts/demo.sh` with `USER_SERVICE_URL=http://127.0.0.1:8080` and load simulation with `KAFKA_BOOTSTRAP_SERVERS=localhost:9092` (match your broker).

### Hybrid (optional)

You can run **only Redis + Kafka** in Docker and still run all Python services natively with `KAFKA_BOOTSTRAP_SERVERS=localhost:9094` (or whatever port you map) and the same commands as above.

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
