"""
DLQ handler: consumes `dead-letter-queue`, persists records for debugging, exposes
Prometheus metrics, and supports replaying DLQ entries back to `raw-events`.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
from pathlib import Path
from typing import Any

import structlog
import uvicorn
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from fastapi import FastAPI
from prometheus_client import Counter, generate_latest
from opentelemetry import trace
from opentelemetry.propagate import extract
from redis.asyncio import Redis
from starlette.responses import PlainTextResponse

from shared.kafka_topics import DEAD_LETTER_QUEUE, RAW_EVENTS
from shared.tracing import headers_from_kafka, init_tracer

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
METRICS_PORT = int(os.getenv("METRICS_PORT", "8005"))
STORAGE = Path(os.getenv("DLQ_STORAGE_PATH", "/tmp/dlq_store.jsonl"))

DLQ_IN = Counter("dlq_handler_messages_total", "DLQ messages persisted")
DLQ_BYTES = Counter("dlq_handler_bytes_total", "Bytes written to DLQ store")
DLQ_REPLAY = Counter("dlq_handler_replay_total", "Events replayed to raw-events")

shutdown_event = asyncio.Event()
redis_client: Redis | None = None
tracer = init_tracer("dlq-handler")

metrics_app = FastAPI()


@metrics_app.get("/health")
async def health():
    return {"status": "ok", "role": "dlq-handler"}


@metrics_app.get("/metrics")
async def metrics():
    return PlainTextResponse(generate_latest().decode("utf-8"), media_type="text/plain")


@metrics_app.post("/replay")
async def replay_dlq(limit: int = 100):
    """Re-inject up to `limit` records from the DLQ file back into raw-events."""
    if not STORAGE.exists():
        return {"replayed": 0}
    producer = AIOKafkaProducer(
        bootstrap_servers=BOOTSTRAP,
        enable_idempotence=True,
        value_serializer=lambda v: json.dumps(v).encode("utf-8"),
    )
    await producer.start()
    replayed = 0
    try:
        lines = STORAGE.read_text(encoding="utf-8").splitlines()
        for line in lines[-limit:]:
            try:
                rec = json.loads(line)
                raw = {
                    "eventId": rec["event_id"],
                    "userId": rec["user_id"],
                    "action": rec["action"],
                }
                await producer.send_and_wait(RAW_EVENTS, value=raw)
                replayed += 1
                DLQ_REPLAY.inc()
            except Exception:
                log.exception("replay_line_failed", line=line[:200])
    finally:
        await producer.stop()
    log.info("dlq_replay_done", replayed=replayed)
    return {"replayed": replayed}


async def run_metrics_server() -> None:
    config = uvicorn.Config(metrics_app, host="0.0.0.0", port=METRICS_PORT, log_level="warning")
    server = uvicorn.Server(config)
    await server.serve()


async def get_redis() -> Redis:
    global redis_client
    if redis_client is None:
        redis_client = Redis.from_url(REDIS_URL, decode_responses=True)
    return redis_client


async def persist_record(raw: bytes) -> None:
    STORAGE.parent.mkdir(parents=True, exist_ok=True)
    line = raw.decode("utf-8").strip() + "\n"
    DLQ_BYTES.inc(len(line.encode("utf-8")))
    with STORAGE.open("a", encoding="utf-8") as f:
        f.write(line)
    redis = await get_redis()
    await redis.incr("stats:dlq_total")
    DLQ_IN.inc()
    try:
        rec = json.loads(line)
        log.error(
            "dlq_record",
            event_id=rec.get("event_id"),
            user_id=rec.get("user_id"),
            reason=rec.get("reason"),
            attempt=rec.get("attempt"),
        )
    except Exception:
        log.error("dlq_unparseable", raw=line[:500])


async def consume_loop() -> None:
    consumer = AIOKafkaConsumer(
        DEAD_LETTER_QUEUE,
        bootstrap_servers=BOOTSTRAP,
        group_id="dlq-handler",
        enable_auto_commit=False,
        auto_offset_reset="earliest",
    )
    await consumer.start()
    try:
        async for msg in consumer:
            if shutdown_event.is_set():
                break
            carrier = headers_from_kafka(msg.headers)
            ctx = extract(carrier) if carrier else None
            with tracer.start_as_current_span("dlq_persist", context=ctx):
                try:
                    await persist_record(msg.value)
                except Exception as e:
                    log.exception("dlq_persist_failed", error=str(e))
                    continue
                await consumer.commit()
    except asyncio.CancelledError:
        pass
    finally:
        await consumer.stop()


async def main_async() -> None:
    tasks = [
        asyncio.create_task(consume_loop()),
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
    if redis_client:
        await redis_client.close()


if __name__ == "__main__":
    asyncio.run(main_async())
