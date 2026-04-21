# Suggested Changes — Hackathon Upgrade Plan

This document covers every significant improvement to make the pipeline production-credible
for a hackathon demo. Each section includes what to change, why it matters, and specific
implementation details tied to the existing code.

---

## 1. Exactly-Once Semantics (EOS)

### What and why
The current pipeline is **at-least-once**: if the stream processor crashes between
writing to `enriched-events` and committing the Kafka offset, the raw event will be
reprocessed and a duplicate enriched event will be emitted. The Redis idempotency key
(`idem:{eventId}`) catches this at the application layer, but that guard only works if
Redis is also durable. Kafka's native EOS removes the race entirely at the transport layer.

### The gap in the current code
`consumer/main.py` commits the Kafka offset *after* `send_and_wait` to `enriched-events`,
but those are two separate operations with no atomicity guarantee between them.

### Implementation

**Producer side** — enable idempotent + transactional producer in `consumer/main.py`
and `retry_worker/main.py`:

```python
kafka_producer = AIOKafkaProducer(
    bootstrap_servers=BOOTSTRAP,
    enable_idempotence=True,          # deduplicates retried sends at broker
    transactional_id="stream-processor-txn-1",   # unique per instance
    value_serializer=...,
)
await kafka_producer.start()
```

Wrap the produce + commit block in a transaction:

```python
async with kafka_producer.transaction():
    await kafka_producer.send_and_wait(ENRICHED_EVENTS, out)
    await consumer.commit()           # offset committed atomically with the produce
```

**Consumer side** — any consumer reading `enriched-events` (including the result service
in change #5) must use `isolation_level="read_committed"` so it never sees messages from
aborted transactions:

```python
consumer = AIOKafkaConsumer(
    ENRICHED_EVENTS,
    isolation_level="read_committed",
    ...
)
```

**Broker config** — add to `kafka` service in `docker-compose.yml`:
```yaml
KAFKA_TRANSACTION_STATE_LOG_REPLICATION_FACTOR: "1"
KAFKA_TRANSACTION_STATE_LOG_MIN_ISR: "1"
```
(Already present from the KRaft migration — no further change needed.)

**Scaling note** — `transactional_id` must be unique per producer *instance*. When
scaling `stream-processor` to multiple replicas, append a replica index or pod name:
`stream-processor-txn-{HOSTNAME}`.

**Files to change:** `services/consumer/main.py`, `services/retry_worker/main.py`,
`infra/docker-compose.yml` (already done).

---

## 2. Consumer Lag Monitoring

### What and why
Events/sec is a vanity metric — **consumer lag** (how many messages are sitting unread
in each partition) is the real production health signal. A lag spike means the consumer
is falling behind; if it keeps growing, you have a scaling problem. Judges who know Kafka
will look for this.

### Implementation

Add a background task to each consuming service that polls lag using `AIOKafkaAdminClient`
every 10 seconds and exposes it as a Prometheus `Gauge`.

Add to `services/consumer/main.py`:

```python
from aiokafka.admin import AIOKafkaAdminClient
from prometheus_client import Gauge

CONSUMER_LAG = Gauge(
    "stream_processor_consumer_lag_messages",
    "Estimated consumer lag in messages",
    ["topic", "partition"],
)

async def lag_monitor_loop() -> None:
    admin = AIOKafkaAdminClient(bootstrap_servers=BOOTSTRAP)
    await admin.start()
    try:
        while not shutdown_event.is_set():
            try:
                # fetch latest offsets for the topic
                topic_partitions = ...  # build TopicPartition list
                end_offsets = await admin.list_offsets(...)
                committed = await admin.list_consumer_group_offsets("stream-processor-raw")
                for tp, end in end_offsets.items():
                    committed_offset = committed.get(tp)
                    if committed_offset is not None:
                        lag = max(0, end.offset - committed_offset.offset)
                        CONSUMER_LAG.labels(tp.topic, tp.partition).set(lag)
            except Exception as e:
                log.warning("lag_poll_failed", error=str(e))
            await asyncio.sleep(10)
    finally:
        await admin.close()
```

Add `lag_monitor_loop()` to the `tasks` list in `main_async()`.

**Dashboard** — add a lag panel in `dashboard/app.py`:

```python
with col1:
    for partition in range(3):
        lag = parse_gauge(sp, "stream_processor_consumer_lag_messages",
                          {"topic": "raw-events", "partition": str(partition)})
        st.metric(f"Lag — partition {partition}", lag)
```

Add a `parse_gauge` helper that matches labeled Prometheus lines:
```python
def parse_gauge(text: str, name: str, labels: dict) -> float:
    label_str = ",".join(f'{k}="{v}"' for k, v in labels.items())
    pat = re.compile(rf'^{re.escape(name)}\{{{re.escape(label_str)}\}}\s+([0-9.eE+-]+)', re.MULTILINE)
    m = pat.search(text)
    return float(m.group(1)) if m else 0.0
```

**Files to change:** `services/consumer/main.py`, `services/retry_worker/main.py`,
`dashboard/app.py`, `requirements.txt` (no new dep — `aiokafka` already exposes admin).

---

## 3. Schema Registry + Avro Contracts

### What and why
Right now every service serializes to plain JSON with no enforced contract. If someone
changes `eventId` to `event_id` in the producer, every downstream consumer silently
breaks. Schema Registry stores versioned Avro schemas; the broker rejects messages that
don't conform. This is the standard production pattern and very impressive to demo.

### Architecture addition
Add Confluent Schema Registry to `docker-compose.yml`:

```yaml
schema-registry:
  image: confluentinc/cp-schema-registry:7.7.1
  depends_on:
    kafka-init:
      condition: service_completed_successfully
  environment:
    SCHEMA_REGISTRY_HOST_NAME: schema-registry
    SCHEMA_REGISTRY_KAFKASTORE_BOOTSTRAP_SERVERS: kafka:9092
    SCHEMA_REGISTRY_LISTENERS: http://0.0.0.0:8081
  ports:
    - "8081:8081"
```

### Avro schemas
Create `shared/avro/` with one `.avsc` file per message type:

`shared/avro/raw_event.avsc`:
```json
{
  "type": "record",
  "name": "RawEvent",
  "namespace": "com.pipeline",
  "fields": [
    {"name": "eventId", "type": "string"},
    {"name": "userId", "type": "int"},
    {"name": "action", "type": "string"}
  ]
}
```

`shared/avro/enriched_event.avsc`:
```json
{
  "type": "record",
  "name": "EnrichedEvent",
  "namespace": "com.pipeline",
  "fields": [
    {"name": "eventId",    "type": "string"},
    {"name": "userId",     "type": "int"},
    {"name": "action",     "type": "string"},
    {"name": "name",       "type": "string"},
    {"name": "email",      "type": "string"},
    {"name": "tier",       "type": "string"},
    {"name": "enrichedAt", "type": "string"},
    {"name": "source",     "type": "string"}
  ]
}
```

### Serializer helper
Add `shared/avro_serde.py`:

```python
import io
import struct
from fastavro import parse_schema, schemaless_writer, schemaless_reader

MAGIC_BYTE = 0

def serialize(schema_id: int, parsed_schema, record: dict) -> bytes:
    buf = io.BytesIO()
    buf.write(struct.pack(">bI", MAGIC_BYTE, schema_id))
    schemaless_writer(buf, parsed_schema, record)
    return buf.getvalue()

def deserialize(parsed_schema, data: bytes) -> dict:
    buf = io.BytesIO(data[5:])   # skip magic byte + schema id
    return schemaless_reader(buf, parsed_schema)
```

Add `fastavro==1.9.7` to `requirements.txt`.

**Files to change:** `infra/docker-compose.yml`, `requirements.txt`, new `shared/avro/`
directory, new `shared/avro_serde.py`, `services/producer/main.py`,
`services/consumer/main.py`, `services/retry_worker/main.py`.

---

## 4. Persistent User Store (PostgreSQL)

### What and why
The user service stores users in a Python dict (`USERS = {1: UserProfile(...), ...}`).
This means every restart loses any users added via `PUT /user/{id}`. Replacing it with
Postgres closes the demo loop — you can add a user via the API, restart the container,
and the user is still there. It also lets you demo the pipeline handling a genuinely
new user that wasn't in the seed data.

### Implementation

**docker-compose.yml** — add Postgres:
```yaml
postgres:
  image: postgres:16-alpine
  environment:
    POSTGRES_USER: pipeline
    POSTGRES_PASSWORD: pipeline
    POSTGRES_DB: users
  ports:
    - "5432:5432"
  volumes:
    - postgres_data:/var/lib/postgresql/data

volumes:
  postgres_data:
```

Add `postgres` to `user-service` `depends_on`.

**user_service/main.py** — replace the in-memory dict with async SQLAlchemy:

```python
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import DeclarativeBase, mapped_column, Mapped
from sqlalchemy import Integer, String, select

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://pipeline:pipeline@postgres/users")
engine = create_async_engine(DATABASE_URL, echo=False)

class Base(DeclarativeBase): pass

class UserRow(Base):
    __tablename__ = "users"
    user_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name:    Mapped[str] = mapped_column(String)
    email:   Mapped[str] = mapped_column(String)
    tier:    Mapped[str] = mapped_column(String, default="standard")
```

On startup, create the table and seed initial rows if empty, then publish all rows to
`user-updates` exactly as before.

**Requirements** — add `sqlalchemy==2.0.36`, `asyncpg==0.30.0`.

**Migrations** — optionally add Alembic. For a hackathon, `Base.metadata.create_all()`
on startup is sufficient and avoids the migration complexity.

**Files to change:** `infra/docker-compose.yml`, `services/user_service/main.py`,
`services/user_service/Dockerfile` (no new dep if pinned in `requirements.txt`),
`requirements.txt`.

---

## 5. Real Result Service

### What and why
Right now `enriched-events` is a Kafka topic that nobody reads — the pipeline produces
into a void. Adding a Result Service that **consumes** `enriched-events` and writes to a
Postgres table closes the loop. You can then expose a `GET /results` endpoint and show
real data appearing in the dashboard. This transforms the demo from "messages flow"
to "here is the actual output."

### New service: `services/result_service/`

`services/result_service/main.py` — consumes `enriched-events`, upserts to Postgres,
exposes a REST query API and Prometheus metrics:

```python
# On each enriched event:
async def handle_enriched(msg_value: bytes) -> None:
    event = EnrichedEvent.model_validate_json(msg_value)
    async with AsyncSession(engine) as session:
        row = EnrichedRecord(
            event_id=event.event_id,
            user_id=event.user_id,
            action=event.action,
            user_name=event.user.get("name") if event.user else None,
            tier=event.user.get("tier") if event.user else None,
            enriched_at=event.enriched_at,
            source=event.source,
        )
        await session.merge(row)   # upsert — idempotent
        await session.commit()
```

Expose:
- `GET /results?limit=50` — recent enriched events (for a live feed in the dashboard)
- `GET /results/stats` — counts by action, tier, source
- `GET /metrics` — Prometheus counters (`result_service_records_written_total`, etc.)
- `GET /health`

### Dashboard integration
Add a 5th column or a new section in `dashboard/app.py` showing:
- Records in the result store (total)
- Breakdown by tier (pro vs standard)
- A live table of the last 10 enriched events using `st.dataframe`

```python
results = httpx.get("http://result-service:8006/results?limit=10", timeout=3).json()
st.dataframe(results)
```

**docker-compose.yml** — add:
```yaml
result-service:
  build:
    context: ..
    dockerfile: services/result_service/Dockerfile
  depends_on:
    kafka-init:
      condition: service_completed_successfully
    postgres:
      condition: service_started
    redis:
      condition: service_started
  environment:
    KAFKA_BOOTSTRAP_SERVERS: kafka:9092
    DATABASE_URL: postgresql+asyncpg://pipeline:pipeline@postgres/pipeline
    METRICS_PORT: "8006"
  ports:
    - "8006:8006"
```

**Files to add:** `services/result_service/main.py`, `services/result_service/Dockerfile`,
`services/result_service/__init__.py`.
**Files to change:** `infra/docker-compose.yml`, `dashboard/app.py`.

---

## 6. OpenTelemetry Distributed Tracing

### What and why
The `trace_id` field already exists on `RetryEnvelope` but is never used for anything.
Wiring up OpenTelemetry lets you show a **trace waterfall** — a single event's journey
from producer → stream processor → retry worker → DLQ, with timing for each hop.
This is visually compelling and shows architectural maturity.

### Components
- **Jaeger** (all-in-one) for trace storage and UI
- `opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-grpc` for instrumentation

**docker-compose.yml** — add Jaeger:
```yaml
jaeger:
  image: jaegertracing/all-in-one:1.60
  environment:
    COLLECTOR_OTLP_ENABLED: "true"
  ports:
    - "16686:16686"   # Jaeger UI
    - "4317:4317"     # OTLP gRPC
```

**Tracing helper** — `shared/tracing.py`:
```python
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

def init_tracer(service_name: str) -> trace.Tracer:
    provider = TracerProvider()
    exporter = OTLPSpanExporter(endpoint=os.getenv("OTLP_ENDPOINT", "jaeger:4317"),
                                insecure=True)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    return trace.get_tracer(service_name)
```

**Propagation across Kafka** — Kafka messages have no built-in header propagation.
The pattern is to inject the trace context into the message's Kafka headers:

Producer side (`services/producer/main.py`):
```python
from opentelemetry.propagate import inject

headers = {}
inject(headers)   # populates traceparent header
await producer.send(RAW_EVENTS, value=payload, headers=list(headers.items()))
```

Consumer side (`services/consumer/main.py`):
```python
from opentelemetry.propagate import extract

ctx = extract({k: v.decode() for k, v in msg.headers})
with tracer.start_as_current_span("handle_raw_event", context=ctx) as span:
    span.set_attribute("event.id", event.event_id)
    span.set_attribute("user.id", event.user_id)
    ...
```

The `trace_id` from `RetryEnvelope` should be replaced with the W3C `traceparent` header
value so the span chain continues through retry attempts.

**Requirements** — add:
```
opentelemetry-sdk==1.28.0
opentelemetry-exporter-otlp-proto-grpc==1.28.0
opentelemetry-propagator-b3==1.28.0
```

**Files to add:** `shared/tracing.py`.
**Files to change:** `infra/docker-compose.yml`, `requirements.txt`,
`services/producer/main.py`, `services/consumer/main.py`,
`services/retry_worker/main.py`, `services/dlq_handler/main.py`.

---

## 7. Non-Blocking Retry with Delay Queue

### What and why
The current retry worker does `await asyncio.sleep(delay)` inside the message handler.
This means one retry in backoff blocks all other retry messages for up to 60 seconds.
At 10 events/sec failing, that's 600 messages piling up behind one sleeping handler.

The correct pattern is a **delay queue**: instead of sleeping, republish the message
to a dedicated `retry-delay-events` topic and have a separate scheduler pick it up
after the deadline has passed.

### Implementation

Add a `retry_after` timestamp to `RetryEnvelope`:

```python
class RetryEnvelope(BaseModel):
    event: RawEvent
    attempt: int = 1
    last_error: str | None = None
    trace_id: str | None = None
    retry_after: float = 0.0    # unix timestamp; process only after this time
```

In `retry_worker/main.py`, instead of sleeping:
```python
new_env = RetryEnvelope(
    event=env.event,
    attempt=env.attempt + 1,
    last_error=err,
    trace_id=env.trace_id,
    retry_after=time.time() + min(2 ** env.attempt, 60.0),
)
await producer.send_and_wait(RETRY_EVENTS, ...)
```

At the top of `handle_retry_message`, check the timestamp before doing any work:
```python
if env.retry_after > time.time():
    # not ready yet — re-queue with same envelope and a short sleep
    await asyncio.sleep(1)
    await producer.send_and_wait(RETRY_EVENTS, msg_value)
    return
```

This is lightweight and unblocks the consumer immediately. A production-grade version
would use a dedicated delay service (e.g., Redis sorted sets by `retry_after` score)
but the re-queue pattern is sufficient for demo scale and is correct.

**Files to change:** `shared/schemas.py`, `services/retry_worker/main.py`.

---

## 8. Circuit Breaker on User Service HTTP Calls

### What and why
`enrichment.py:fetch_user_http` retries the User Service up to 3 times with backoff,
but if the service is down it still tries every single time for every single event.
At 500 events/sec, that's 1500 HTTP calls/sec against a dead service. A circuit breaker
opens after N consecutive failures and fast-fails for a cooldown period, preventing
thundering herd.

### Implementation

Add a simple circuit breaker to `shared/enrichment.py`:

```python
import time
from dataclasses import dataclass, field

@dataclass
class CircuitBreaker:
    failure_threshold: int = 5
    cooldown_sec: float = 30.0
    _failures: int = 0
    _opened_at: float = 0.0

    def is_open(self) -> bool:
        if self._opened_at and time.monotonic() - self._opened_at < self.cooldown_sec:
            return True
        if self._opened_at:            # cooldown elapsed — reset to half-open
            self._opened_at = 0.0
            self._failures = 0
        return False

    def record_failure(self) -> None:
        self._failures += 1
        if self._failures >= self.failure_threshold:
            self._opened_at = time.monotonic()

    def record_success(self) -> None:
        self._failures = 0
        self._opened_at = 0.0

user_service_cb = CircuitBreaker()
```

Wrap `fetch_user_http`:
```python
async def fetch_user_http(...) -> UserProfile | None:
    if user_service_cb.is_open():
        log.warning("circuit_breaker_open", user_id=user_id)
        return None
    ...
    # on success:
    user_service_cb.record_success()
    return prof
    # on all retries exhausted:
    user_service_cb.record_failure()
    return None
```

Expose the circuit breaker state in `/metrics` as a Gauge
(`user_service_circuit_breaker_open 0/1`).

**Files to change:** `shared/enrichment.py`.

---

## 9. Horizontal Scaling Demo

### What and why
The README mentions horizontal scaling but there's no way to actually demo it without
extra config. With 3 partitions per topic and consumer groups already set up, you can
spin up two `stream-processor` instances and Kafka will rebalance — partition 0 goes to
replica 1, partitions 1 and 2 to replica 2. Showing this live is powerful.

### Changes needed

**docker-compose.yml** — remove the fixed host port from `stream-processor` (you can't
bind two containers to the same host port) and use `deploy.replicas`:

```yaml
stream-processor:
  ...
  deploy:
    replicas: 2
  ports: []    # remove "8002:8002" — use service discovery inside the network
```

For metrics scraping to still work with multiple replicas, add a simple Nginx or
Prometheus file-based service discovery, or just scrape both by IP from the dashboard.
For a hackathon demo, removing the port and showing logs from two instances rebalancing
is enough.

**Transactional IDs** — each replica must have a unique `transactional_id`. Inject the
hostname:
```python
import socket
TRANSACTIONAL_ID = f"stream-processor-txn-{socket.gethostname()}"
```

**Files to change:** `infra/docker-compose.yml`, `services/consumer/main.py`.

---

## 10. DLQ Replay Endpoint

### What and why
Dead-lettered events are currently written to a JSONL file and forgotten. A **replay
endpoint** lets you re-inject DLQ events back into `raw-events` after you've fixed the
root cause. This closes the operational loop and makes a great live demo moment: show
events failing → going to DLQ → fix the user → replay → watch enriched count climb.

### Implementation

Add to `services/dlq_handler/main.py`:

```python
@metrics_app.post("/replay")
async def replay_dlq(limit: int = 100):
    """Re-inject up to `limit` records from the DLQ file back into raw-events."""
    if not STORAGE.exists():
        return {"replayed": 0}
    producer = AIOKafkaProducer(bootstrap_servers=BOOTSTRAP,
                                value_serializer=lambda v: json.dumps(v).encode())
    await producer.start()
    replayed = 0
    lines = STORAGE.read_text().splitlines()
    for line in lines[-limit:]:
        try:
            rec = json.loads(line)
            raw = {"eventId": rec["event_id"],
                   "userId":  rec["user_id"],
                   "action":  rec["action"]}
            await producer.send_and_wait(RAW_EVENTS, raw)
            replayed += 1
        except Exception:
            pass
    await producer.stop()
    return {"replayed": replayed}
```

Add `from shared.kafka_topics import RAW_EVENTS` to the DLQ handler imports.

**Dashboard** — add a replay button in `dashboard/app.py`:
```python
if st.button("Replay DLQ"):
    r = httpx.post("http://dlq-handler:8005/replay", timeout=10)
    st.success(f"Replayed {r.json().get('replayed', 0)} events")
```

**Files to change:** `services/dlq_handler/main.py`, `dashboard/app.py`.

---

## Summary Table

| # | Change | Effort | Demo impact |
|---|--------|--------|-------------|
| 1 | Exactly-once semantics | Medium | High — fixes the fundamental guarantee |
| 2 | Consumer lag monitoring | Low | High — the real production health signal |
| 3 | Schema Registry + Avro | High | High — enforced contracts, ecosystem standard |
| 4 | Postgres user store | Medium | Medium — persistence across restarts |
| 5 | Result Service | Medium | High — closes the pipeline loop visually |
| 6 | OpenTelemetry tracing | Medium | High — trace waterfall is visually compelling |
| 7 | Non-blocking retry queue | Low | Medium — correctness fix, good to explain |
| 8 | Circuit breaker | Low | Medium — shows operational awareness |
| 9 | Horizontal scaling demo | Low | High — Kafka partition rebalancing live |
| 10 | DLQ replay endpoint | Low | High — live self-healing demo moment |

**Recommended priority for a time-constrained hackathon:**
Do 2 (lag), 10 (DLQ replay), 9 (scaling demo), and 5 (result service) first — they are
low-to-medium effort and produce the most visible, explainable demo moments. Then add
1 (EOS) and 6 (tracing) if time allows, as they show the deepest technical knowledge.
