"""
Stream Processor: consumes `raw-events` and `user-updates`, maintains Redis-backed
local user projection + idempotency, produces `enriched-events` or `retry-events`.

Supports optional Kafka transactions (EOS) wrapping outbound produces + offset commit,
Prometheus consumer lag, optional Avro + Schema Registry, and OpenTelemetry propagation.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import socket
import time
import uuid
from collections import OrderedDict
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
from shared.kafka_topics import ENRICHED_EVENTS, RAW_EVENTS, RETRY_EVENTS, USER_UPDATES
from shared.redis_keys import idempotency_key, user_profile_key
from shared.schema_registry import ensure_schema_id, load_avro_json
from shared.schemas import EnrichedEvent, RawEvent, RetryEnvelope, UserUpdateMessage
from shared.tracing import headers_from_kafka, init_tracer
from shared.avro_serde import confluent_decode, confluent_encode, parse_schema

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
METRICS_PORT = int(os.getenv("METRICS_PORT", "8002"))
USER_HTTP_RETRIES = int(os.getenv("USER_HTTP_RETRIES", "3"))
CACHE_TTL_SEC = float(os.getenv("LOCAL_CACHE_TTL_SEC", "30"))
CACHE_MAX = int(os.getenv("LOCAL_CACHE_MAX", "10000"))
RAW_GROUP = "stream-processor-raw"
USER_GROUP = "stream-processor-user-updates"
KAFKA_ENABLE_TXN = os.getenv("KAFKA_ENABLE_TXN", "1").lower() in ("1", "true", "yes")
USE_AVRO = os.getenv("KAFKA_USE_AVRO", "0").lower() in ("1", "true", "yes")
TRANSACTIONAL_ID = os.getenv(
    "KAFKA_TRANSACTIONAL_ID",
    f"stream-processor-txn-{socket.gethostname()}",
)

tracer = init_tracer("stream-processor")

EVENTS_IN = Counter("stream_processor_events_consumed_total", "Raw events consumed")
EVENTS_DUPLICATE = Counter("stream_processor_duplicate_events_total", "Skipped idempotent duplicates")
EVENTS_ENRICHED = Counter("stream_processor_enriched_events_total", "Events written to enriched-events")
RETRY_OUT = Counter("stream_processor_retry_published_total", "Events sent to retry topic")
USER_UPDATES_APPLIED = Counter("stream_processor_user_updates_applied_total", "User snapshots applied")
PROC_LATENCY = Histogram(
    "stream_processor_processing_seconds",
    "End-to-end processing time for raw events",
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
)
ERRORS = Counter("stream_processor_errors_total", "Unexpected handler errors", ["stage"])
CONSUMER_LAG = Gauge(
    "stream_processor_consumer_lag_messages",
    "Approximate consumer lag (log end minus committed)",
    ["topic", "partition"],
)

shutdown_event = asyncio.Event()
redis_client: Redis | None = None
http_client: httpx.AsyncClient | None = None
txn_producer: AIOKafkaProducer | None = None

_avro_raw_parsed: dict[str, Any] | None = None
_avro_enriched_parsed: dict[str, Any] | None = None
_raw_schema_id: int | None = None
_enriched_schema_id: int | None = None


class LRUCache:
    def __init__(self, maxsize: int, ttl_sec: float) -> None:
        self._ttl = ttl_sec
        self._max = maxsize
        self._data: OrderedDict[int, tuple[dict[str, Any], float]] = OrderedDict()

    def get(self, user_id: int) -> dict[str, Any] | None:
        now = time.monotonic()
        if user_id not in self._data:
            return None
        val, exp = self._data[user_id]
        if exp < now:
            del self._data[user_id]
            return None
        self._data.move_to_end(user_id)
        return val

    def set(self, user_id: int, obj: dict[str, Any]) -> None:
        now = time.monotonic()
        self._data[user_id] = (obj, now + self._ttl)
        self._data.move_to_end(user_id)
        while len(self._data) > self._max:
            self._data.popitem(last=False)

    def invalidate(self, user_id: int) -> None:
        self._data.pop(user_id, None)


lru_user_cache = LRUCache(CACHE_MAX, CACHE_TTL_SEC)


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
    global _avro_raw_parsed, _avro_enriched_parsed, _raw_schema_id, _enriched_schema_id
    if not USE_AVRO:
        return
    raw_s = load_avro_json("raw_event.avsc")
    en_s = load_avro_json("enriched_event.avsc")
    _avro_raw_parsed = parse_schema(raw_s)
    _avro_enriched_parsed = parse_schema(en_s)
    _raw_schema_id = await ensure_schema_id("raw-events", raw_s)
    _enriched_schema_id = await ensure_schema_id("enriched-events", en_s)


async def get_txn_producer() -> AIOKafkaProducer:
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


def decode_raw_value(data: bytes) -> RawEvent:
    if USE_AVRO and _avro_raw_parsed is not None:
        d = confluent_decode(_avro_raw_parsed, data)
        return RawEvent.model_validate(d)
    return RawEvent.model_validate_json(data.decode("utf-8"))


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


async def handle_user_update(msg_value: bytes) -> None:
    redis = await get_redis()
    data = json.loads(msg_value.decode("utf-8"))
    upd = UserUpdateMessage.model_validate(data)
    uid = upd.user.user_id
    await redis.set(user_profile_key(uid), json.dumps(upd.user.to_enrichment_dict()))
    lru_user_cache.invalidate(uid)
    USER_UPDATES_APPLIED.inc()
    log.debug("user_projection_updated", user_id=uid)


async def user_updates_loop() -> None:
    consumer = AIOKafkaConsumer(
        USER_UPDATES,
        bootstrap_servers=BOOTSTRAP,
        group_id=USER_GROUP,
        enable_auto_commit=False,
        auto_offset_reset="earliest",
    )
    await consumer.start()
    try:
        async for msg in consumer:
            if shutdown_event.is_set():
                break
            try:
                await handle_user_update(msg.value)
            except Exception as e:
                ERRORS.labels("user_update").inc()
                log.exception("user_update_failed", error=str(e))
            finally:
                await consumer.commit()
    except asyncio.CancelledError:
        pass
    finally:
        await consumer.stop()


async def raw_events_loop() -> None:
    consumer = AIOKafkaConsumer(
        RAW_EVENTS,
        bootstrap_servers=BOOTSTRAP,
        group_id=RAW_GROUP,
        enable_auto_commit=False,
        auto_offset_reset="earliest",
        isolation_level="read_uncommitted",
    )
    await consumer.start()
    producer = await get_txn_producer()
    try:
        async for msg in consumer:
            if shutdown_event.is_set():
                break
            EVENTS_IN.inc()
            carrier = headers_from_kafka(msg.headers)
            try:
                ctx = extract(carrier) if carrier else None
                with tracer.start_as_current_span("handle_raw_event", context=ctx):
                    span = trace.get_current_span()
                    redis = await get_redis()
                    http = get_http()
                    t0 = time.perf_counter()
                    event = decode_raw_value(msg.value)
                    span.set_attribute("event.id", event.event_id)
                    span.set_attribute("user.id", event.user_id)

                    ikey = idempotency_key(event.event_id)
                    state = await redis.get(ikey)
                    if state in ("enriched", "dlq"):
                        EVENTS_DUPLICATE.inc()
                        log.info("skip_duplicate_or_terminal", event_id=event.event_id, state=state)
                        if KAFKA_ENABLE_TXN:
                            async with producer.transaction():
                                tp = TopicPartition(msg.topic, msg.partition)
                                await producer.send_offsets_to_transaction(
                                    {tp: OffsetAndMetadata(msg.offset + 1, "")},
                                    RAW_GROUP,
                                )
                        else:
                            await consumer.commit()
                        continue

                    hit = lru_user_cache.get(event.user_id)
                    if hit is not None:
                        user_dict, err = hit, None
                    else:
                        user_dict, err = await resolve_user(
                            redis,
                            http,
                            USER_SERVICE_URL,
                            event.user_id,
                            None,
                            CACHE_TTL_SEC,
                            USER_HTTP_RETRIES,
                        )
                        if user_dict is not None:
                            lru_user_cache.set(event.user_id, user_dict)

                    if user_dict is None:
                        env = RetryEnvelope(
                            event=event,
                            attempt=1,
                            last_error=err,
                            trace_id=str(uuid.uuid4()),
                            retry_after=0.0,
                        )
                        payload = json.dumps(env.model_dump(mode="json", by_alias=True)).encode("utf-8")
                        if KAFKA_ENABLE_TXN:
                            async with producer.transaction():
                                await producer.send_and_wait(RETRY_EVENTS, payload)
                                tp = TopicPartition(msg.topic, msg.partition)
                                await producer.send_offsets_to_transaction(
                                    {tp: OffsetAndMetadata(msg.offset + 1, "")},
                                    RAW_GROUP,
                                )
                        else:
                            await producer.send_and_wait(RETRY_EVENTS, payload)
                            await consumer.commit()
                        RETRY_OUT.inc()
                        log.warning("event_routed_to_retry", event_id=event.event_id, error=err)
                        continue

                    enriched = EnrichedEvent(
                        eventId=event.event_id,
                        userId=event.user_id,
                        action=event.action,
                        user=user_dict,
                        source="stream-processor",
                    )
                    out = encode_enriched(enriched)

                    if KAFKA_ENABLE_TXN:
                        async with producer.transaction():
                            await producer.send_and_wait(ENRICHED_EVENTS, out)
                            tp = TopicPartition(msg.topic, msg.partition)
                            await producer.send_offsets_to_transaction(
                                {tp: OffsetAndMetadata(msg.offset + 1, "")},
                                RAW_GROUP,
                            )
                    else:
                        await producer.send_and_wait(ENRICHED_EVENTS, out)
                        await consumer.commit()

                    await redis.set(ikey, "enriched", ex=86400 * 7)
                    EVENTS_ENRICHED.inc()
                    PROC_LATENCY.observe(time.perf_counter() - t0)
                    log.info("event_enriched", event_id=event.event_id, user_id=event.user_id)
            except Exception as e:
                ERRORS.labels("raw_event").inc()
                log.exception("raw_event_failed", error=str(e))
                continue
    except asyncio.CancelledError:
        pass
    finally:
        await consumer.stop()


metrics_app = FastAPI()


@metrics_app.get("/health")
async def health():
    return {"status": "ok", "role": "stream-processor"}


@metrics_app.get("/metrics")
async def metrics():
    return PlainTextResponse(generate_latest().decode("utf-8"), media_type="text/plain")


async def run_metrics_server() -> None:
    config = uvicorn.Config(metrics_app, host="0.0.0.0", port=METRICS_PORT, log_level="warning")
    server = uvicorn.Server(config)
    await server.serve()


async def main_async() -> None:
    await init_avro()
    await get_txn_producer()
    lag_shutdown = asyncio.Event()

    async def lag_wrapped() -> None:
        await lag_loop(
            BOOTSTRAP,
            lag_shutdown,
            [
                (RAW_EVENTS, RAW_GROUP, CONSUMER_LAG),
                (USER_UPDATES, USER_GROUP, CONSUMER_LAG),
            ],
        )

    tasks = [
        asyncio.create_task(user_updates_loop()),
        asyncio.create_task(raw_events_loop()),
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
    p = txn_producer
    if p:
        await p.stop()
    r = redis_client
    if r:
        await r.close()
    h = http_client
    if h:
        await h.aclose()


if __name__ == "__main__":
    asyncio.run(main_async())
