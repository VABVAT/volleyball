"""
Retry worker: consumes `retry-events`, exponential backoff via retry_after,
re-runs enrichment, produces to enriched-events or DLQ. Optional Kafka transactions (EOS).
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import socket
import time
from typing import Any

import httpx
import structlog
import uvicorn
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from aiokafka.structs import OffsetAndMetadata, TopicPartition
from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.propagate import extract
from prometheus_client import Counter, Gauge, Histogram, generate_latest
from redis.asyncio import Redis
from starlette.responses import PlainTextResponse

from shared.enrichment import resolve_user
from shared.kafka_lag import lag_loop
from shared.kafka_topics import DEAD_LETTER_QUEUE, ENRICHED_EVENTS, RETRY_EVENTS
from shared.redis_keys import idempotency_key
from shared.schema_registry import ensure_schema_id, load_avro_json
from shared.schemas import DeadLetterRecord, EnrichedEvent, RetryEnvelope
from shared.tracing import headers_from_kafka, init_tracer
from shared.avro_serde import confluent_encode, parse_schema

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.dev.ConsoleRenderer(),
    ],
)
log = structlog.get_logger()

BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
USER_SERVICE_URL = os.getenv("USER_SERVICE_URL", "http://localhost:8080")
METRICS_PORT = int(os.getenv("METRICS_PORT", "8004"))
MAX_RETRY_ATTEMPTS = int(os.getenv("MAX_RETRY_ATTEMPTS", "3"))
USER_HTTP_RETRIES = int(os.getenv("USER_HTTP_RETRIES", "3"))
CACHE_TTL_SEC = float(os.getenv("LOCAL_CACHE_TTL_SEC", "30"))
RETRY_GROUP = "retry-worker"
KAFKA_ENABLE_TXN = os.getenv("KAFKA_ENABLE_TXN", "1").lower() in ("1", "true", "yes")
USE_AVRO = os.getenv("KAFKA_USE_AVRO", "0").lower() in ("1", "true", "yes")
TRANSACTIONAL_ID = os.getenv(
    "KAFKA_TRANSACTIONAL_ID",
    f"retry-worker-txn-{socket.gethostname()}",
)
# Backoff between worker passes (floor avoids tight Kafka republish loops under load).
RETRY_MIN_BACKOFF_SEC = float(os.getenv("RETRY_MIN_BACKOFF_SEC", "1.0"))
RETRY_MAX_BACKOFF_SEC = float(os.getenv("RETRY_MAX_BACKOFF_SEC", "120.0"))

tracer = init_tracer("retry-worker")

RETRY_IN = Counter("retry_worker_messages_consumed_total", "Retry envelopes consumed")
RETRY_SUCCESS = Counter("retry_worker_success_total", "Resolved after retry")
RETRY_REPUBLISH = Counter("retry_worker_republished_total", "Sent back to retry topic")
DLQ_OUT = Counter("retry_worker_dlq_published_total", "Sent to DLQ topic")
RETRY_LATENCY = Histogram(
    "retry_worker_processing_seconds",
    "Processing time for retry messages",
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 15, 60, 120),
)
ERRORS = Counter("retry_worker_errors_total", "Unexpected errors", ["stage"])
RETRY_LAG = Gauge(
    "retry_worker_consumer_lag_messages",
    "Approximate consumer lag for retry-events",
    ["topic", "partition"],
)

shutdown_event = asyncio.Event()
redis_client: Redis | None = None
http_client: httpx.AsyncClient | None = None
txn_producer: AIOKafkaProducer | None = None
_avro_enriched_parsed: dict[str, Any] | None = None
_enriched_schema_id: int | None = None


async def _sleep_until_retry_after(retry_after: float) -> None:
    """Block this partition until retry_after; chunked sleeps so shutdown stays responsive."""
    while time.time() < retry_after:
        if shutdown_event.is_set():
            raise asyncio.CancelledError()
        remaining = retry_after - time.time()
        await asyncio.sleep(min(max(remaining, 0.001), 1.0))


async def get_redis() -> Redis:
    global redis_client
    if redis_client is None:
        redis_client = Redis.from_url(REDIS_URL, decode_responses=True)
    return redis_client


def get_http() -> httpx.AsyncClient:
    global http_client
    if http_client is None:
        http_client = httpx.AsyncClient()
    return http_client


async def init_avro() -> None:
    global _avro_enriched_parsed, _enriched_schema_id
    if not USE_AVRO:
        return
    en_s = load_avro_json("enriched_event.avsc")
    _avro_enriched_parsed = parse_schema(en_s)
    _enriched_schema_id = await ensure_schema_id("enriched-events", en_s)


async def get_producer() -> AIOKafkaProducer:
    global txn_producer
    if txn_producer is None:
        kwargs: dict[str, Any] = {
            "bootstrap_servers": BOOTSTRAP,
            "enable_idempotence": True,
            "value_serializer": lambda v: v if isinstance(v, bytes) else str(v).encode("utf-8"),
        }
        if KAFKA_ENABLE_TXN:
            kwargs["transactional_id"] = TRANSACTIONAL_ID
        txn_producer = AIOKafkaProducer(**kwargs)
        await txn_producer.start()
    return txn_producer


def encode_enriched(ev: EnrichedEvent) -> bytes:
    if USE_AVRO and _avro_enriched_parsed is not None and _enriched_schema_id is not None:
        u = ev.user or {}
        rec = {
            "eventId": ev.event_id,
            "userId": ev.user_id,
            "action": ev.action,
            "name": str(u.get("name", "")),
            "email": str(u.get("email", "")),
            "tier": str(u.get("tier", "standard")),
            "enrichedAt": ev.enriched_at,
            "source": ev.source,
        }
        return confluent_encode(_enriched_schema_id, _avro_enriched_parsed, rec)
    return json.dumps(ev.model_dump(mode="json", by_alias=True)).encode("utf-8")


async def publish_dlq(producer: AIOKafkaProducer, envelope: RetryEnvelope, reason: str | None) -> None:
    rec = DeadLetterRecord(
        event_id=envelope.event.event_id,
        user_id=envelope.event.user_id,
        action=envelope.event.action,
        reason=reason or "unknown",
        attempt=envelope.attempt,
        payload=envelope.model_dump(mode="json", by_alias=True),
    )
    raw = json.dumps(rec.model_dump(mode="json")).encode("utf-8")
    await producer.send_and_wait(DEAD_LETTER_QUEUE, raw)
    DLQ_OUT.inc()
    log.warning("sent_to_dlq", event_id=envelope.event.event_id, attempt=envelope.attempt)


async def handle_retry_message(
    producer: AIOKafkaProducer,
    msg,
) -> None:
    redis = await get_redis()
    http = get_http()
    t0 = time.perf_counter()
    carrier = headers_from_kafka(msg.headers)
    ctx = extract(carrier) if carrier else None
    with tracer.start_as_current_span("handle_retry_message", context=ctx):
        span = trace.get_current_span()
        env = RetryEnvelope.model_validate_json(msg.value)
        span.set_attribute("event.id", env.event.event_id)

        await _sleep_until_retry_after(env.retry_after)

        ikey = idempotency_key(env.event.event_id)
        state = await redis.get(ikey)
        if state in ("enriched", "dlq"):
            log.info("retry_skip_terminal", event_id=env.event.event_id, state=state)
            return

        user_dict, err = await resolve_user(
            redis,
            http,
            USER_SERVICE_URL,
            env.event.user_id,
            None,
            CACHE_TTL_SEC,
            USER_HTTP_RETRIES,
        )
        if user_dict is not None:
            enriched = EnrichedEvent(
                eventId=env.event.event_id,
                userId=env.event.user_id,
                action=env.event.action,
                user=user_dict,
                source="retry-worker",
            )
            out = encode_enriched(enriched)
            await producer.send_and_wait(ENRICHED_EVENTS, out)
            await redis.set(ikey, "enriched", ex=86400 * 7)
            RETRY_SUCCESS.inc()
            RETRY_LATENCY.observe(time.perf_counter() - t0)
            log.info("retry_succeeded", event_id=env.event.event_id)
            return

        if env.attempt >= MAX_RETRY_ATTEMPTS:
            await publish_dlq(producer, env, err)
            await redis.set(ikey, "dlq", ex=86400 * 7)
            return

        now = time.time()
        delay = max(
            RETRY_MIN_BACKOFF_SEC,
            min(2.0**env.attempt, RETRY_MAX_BACKOFF_SEC),
        )
        new_env = RetryEnvelope(
            event=env.event,
            attempt=env.attempt + 1,
            last_error=err,
            trace_id=env.trace_id,
            retry_after=now + delay,
        )
        payload = json.dumps(new_env.model_dump(mode="json", by_alias=True)).encode("utf-8")
        await producer.send_and_wait(RETRY_EVENTS, payload)
        RETRY_REPUBLISH.inc()


async def retry_loop() -> None:
    await init_avro()
    producer = await get_producer()
    consumer = AIOKafkaConsumer(
        RETRY_EVENTS,
        bootstrap_servers=BOOTSTRAP,
        group_id=RETRY_GROUP,
        enable_auto_commit=False,
        auto_offset_reset="earliest",
        isolation_level="read_uncommitted",
    )
    await consumer.start()
    try:
        async for msg in consumer:
            if shutdown_event.is_set():
                break
            RETRY_IN.inc()
            try:
                if KAFKA_ENABLE_TXN:
                    async with producer.transaction():
                        await handle_retry_message(producer, msg)
                        tp = TopicPartition(msg.topic, msg.partition)
                        await producer.send_offsets_to_transaction(
                            {tp: OffsetAndMetadata(msg.offset + 1, "")},
                            RETRY_GROUP,
                        )
                else:
                    await handle_retry_message(producer, msg)
                    await consumer.commit()
            except Exception as e:
                ERRORS.labels("handle").inc()
                log.exception("retry_handle_failed", error=str(e))
                continue
    except asyncio.CancelledError:
        pass
    finally:
        await consumer.stop()


metrics_app = FastAPI()


@metrics_app.get("/health")
async def health():
    return {"status": "ok", "role": "retry-worker"}


@metrics_app.get("/metrics")
async def metrics():
    return PlainTextResponse(generate_latest().decode("utf-8"), media_type="text/plain")


async def run_metrics_server() -> None:
    config = uvicorn.Config(metrics_app, host="0.0.0.0", port=METRICS_PORT, log_level="warning")
    server = uvicorn.Server(config)
    await server.serve()


async def main_async() -> None:
    await get_producer()
    lag_shutdown = asyncio.Event()

    async def lag_wrapped() -> None:
        await lag_loop(
            BOOTSTRAP,
            lag_shutdown,
            [(RETRY_EVENTS, RETRY_GROUP, RETRY_LAG)],
        )

    tasks = [
        asyncio.create_task(retry_loop()),
        asyncio.create_task(run_metrics_server()),
        asyncio.create_task(lag_wrapped()),
    ]

    def _stop(*_: Any) -> None:
        log.info("shutdown_signal")
        shutdown_event.set()
        lag_shutdown.set()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _stop)
        except NotImplementedError:
            pass

    await shutdown_event.wait()
    lag_shutdown.set()
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    if txn_producer:
        await txn_producer.stop()
    if redis_client:
        await redis_client.close()
    if http_client:
        await http_client.aclose()


if __name__ == "__main__":
    asyncio.run(main_async())
