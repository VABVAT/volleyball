"""
Retry worker: consumes `retry-events`, applies exponential backoff between attempts,
re-runs enrichment with the same Redis projection + HTTP fallback, and routes to
`dead-letter-queue` after MAX_RETRY_ATTEMPTS failures.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import time
from typing import Any

import httpx
import structlog
import uvicorn
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from fastapi import FastAPI
from prometheus_client import Counter, Histogram, generate_latest
from redis.asyncio import Redis
from starlette.responses import PlainTextResponse

from shared.enrichment import resolve_user
from shared.kafka_topics import DEAD_LETTER_QUEUE, ENRICHED_EVENTS, RETRY_EVENTS
from shared.redis_keys import idempotency_key
from shared.schemas import DeadLetterRecord, EnrichedEvent, RetryEnvelope

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

shutdown_event = asyncio.Event()
redis_client: Redis | None = None
http_client: httpx.AsyncClient | None = None
kafka_producer: AIOKafkaProducer | None = None


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


async def get_producer() -> AIOKafkaProducer:
    global kafka_producer
    if kafka_producer is None:
        kafka_producer = AIOKafkaProducer(
            bootstrap_servers=BOOTSTRAP,
            value_serializer=lambda v: v if isinstance(v, bytes) else str(v).encode("utf-8"),
        )
        await kafka_producer.start()
    return kafka_producer


async def publish_dlq(envelope: RetryEnvelope, reason: str | None) -> None:
    producer = await get_producer()
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


async def handle_retry_message(msg_value: bytes) -> None:
    redis = await get_redis()
    producer = await get_producer()
    http = get_http()
    t0 = time.perf_counter()
    env = RetryEnvelope.model_validate_json(msg_value)
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
        out = json.dumps(enriched.model_dump(mode="json", by_alias=True)).encode("utf-8")
        await producer.send_and_wait(ENRICHED_EVENTS, out)
        await redis.set(ikey, "enriched", ex=86400 * 7)
        RETRY_SUCCESS.inc()
        RETRY_LATENCY.observe(time.perf_counter() - t0)
        log.info("retry_succeeded", event_id=env.event.event_id)
        return

    if env.attempt >= MAX_RETRY_ATTEMPTS:
        await publish_dlq(env, err)
        await redis.set(ikey, "dlq", ex=86400 * 7)
        return

    delay = min(2**env.attempt, 60.0)
    log.warning(
        "retry_backoff",
        event_id=env.event.event_id,
        next_attempt=env.attempt + 1,
        sleep_s=delay,
        error=err,
    )
    await asyncio.sleep(delay)
    new_env = RetryEnvelope(
        event=env.event,
        attempt=env.attempt + 1,
        last_error=err,
        trace_id=env.trace_id,
    )
    payload = json.dumps(new_env.model_dump(mode="json", by_alias=True)).encode("utf-8")
    await producer.send_and_wait(RETRY_EVENTS, payload)
    RETRY_REPUBLISH.inc()


async def retry_loop() -> None:
    consumer = AIOKafkaConsumer(
        RETRY_EVENTS,
        bootstrap_servers=BOOTSTRAP,
        group_id="retry-worker",
        enable_auto_commit=False,
        auto_offset_reset="earliest",
    )
    await consumer.start()
    try:
        async for msg in consumer:
            if shutdown_event.is_set():
                break
            RETRY_IN.inc()
            try:
                await handle_retry_message(msg.value)
            except Exception as e:
                ERRORS.labels("handle").inc()
                log.exception("retry_handle_failed", error=str(e))
                continue
            await consumer.commit()
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
    tasks = [
        asyncio.create_task(retry_loop()),
        asyncio.create_task(run_metrics_server()),
    ]

    def _stop(*_: Any) -> None:
        log.info("shutdown_signal")
        shutdown_event.set()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _stop)
        except NotImplementedError:
            pass

    await shutdown_event.wait()
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    if kafka_producer:
        await kafka_producer.stop()
    if redis_client:
        await redis_client.close()
    if http_client:
        await http_client.aclose()


if __name__ == "__main__":
    asyncio.run(main_async())
