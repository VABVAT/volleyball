"""
DLQ handler: consumes `dead-letter-queue`, persists records for debugging, and exposes
Prometheus metrics (including a monotonic DLQ counter).
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
from aiokafka import AIOKafkaConsumer
from fastapi import FastAPI
from prometheus_client import Counter, generate_latest
from redis.asyncio import Redis
from starlette.responses import PlainTextResponse

from shared.kafka_topics import DEAD_LETTER_QUEUE

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

shutdown_event = asyncio.Event()
redis_client: Redis | None = None

metrics_app = FastAPI()


@metrics_app.get("/health")
async def health():
    return {"status": "ok", "role": "dlq-handler"}


@metrics_app.get("/metrics")
async def metrics():
    return PlainTextResponse(generate_latest().decode("utf-8"), media_type="text/plain")


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
