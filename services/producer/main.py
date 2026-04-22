"""
Mock producer: publishes synthetic raw events to `raw-events`.
Optional OpenTelemetry context injection and Avro + Schema Registry encoding.
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import signal
import uuid

import structlog
import uvicorn
from aiokafka import AIOKafkaProducer
from fastapi import FastAPI, Query
from opentelemetry import trace as otel_trace
from opentelemetry.propagate import inject

from shared.kafka_topics import RAW_EVENTS
from shared.tracing import init_tracer
from shared.schema_registry import ensure_schema_id, load_avro_json
from shared.tracing import kafka_headers_from_carrier
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
INTERVAL = float(os.getenv("PRODUCER_INTERVAL_SEC", "0.2"))
DUPLICATE_EVERY_N = int(os.getenv("DUPLICATE_EVERY_N", "15"))
ADMIN_PORT = int(os.getenv("PRODUCER_ADMIN_PORT", "8010"))
USE_AVRO = os.getenv("KAFKA_USE_AVRO", "0").lower() in ("1", "true", "yes")

USER_IDS = [1, 2, 3, 123]
ACTIONS = ["click", "view", "purchase", "signup"]

shutdown_event = asyncio.Event()
settings_lock = asyncio.Lock()
events_per_sec: float = max(0.1, 1.0 / max(0.001, INTERVAL))
duplicate_every_n: int = max(1, DUPLICATE_EVERY_N)

tracer = init_tracer("producer")
_avro_parsed: dict | None = None
_raw_schema_id: int | None = None

admin_app = FastAPI(title="Producer Admin")


@admin_app.get("/admin/speed")
async def get_speed():
    async with settings_lock:
        return {"events_per_sec": events_per_sec}


@admin_app.post("/admin/speed")
async def set_speed(eps: float = Query(..., gt=0.0, le=5000.0)):
    global events_per_sec
    async with settings_lock:
        events_per_sec = float(eps)
        return {"events_per_sec": events_per_sec}


@admin_app.get("/admin/duplicates")
async def get_duplicates():
    async with settings_lock:
        return {"duplicate_every_n": duplicate_every_n}


@admin_app.post("/admin/duplicates")
async def set_duplicates(every_n: int = Query(..., ge=1, le=1_000_000)):
    global duplicate_every_n
    async with settings_lock:
        duplicate_every_n = int(every_n)
        return {"duplicate_every_n": duplicate_every_n}


@admin_app.get("/health")
async def health():
    return {"status": "ok"}


async def run_admin_server() -> None:
    config = uvicorn.Config(admin_app, host="0.0.0.0", port=ADMIN_PORT, log_level="warning")
    server = uvicorn.Server(config)
    await server.serve()


async def init_avro() -> None:
    global _avro_parsed, _raw_schema_id
    if not USE_AVRO:
        return
    raw_s = load_avro_json("raw_event.avsc")
    _avro_parsed = parse_schema(raw_s)
    _raw_schema_id = await ensure_schema_id("raw-events", raw_s)


def encode_payload(payload: dict) -> bytes:
    if USE_AVRO and _avro_parsed is not None and _raw_schema_id is not None:
        return confluent_encode(_raw_schema_id, _avro_parsed, payload)
    return json.dumps(payload).encode("utf-8")


async def run() -> None:
    await init_avro()
    producer = AIOKafkaProducer(
        bootstrap_servers=BOOTSTRAP,
        value_serializer=lambda v: v if isinstance(v, bytes) else v,
    )
    await producer.start()
    n = 0
    last_duplicate_id: str | None = None
    try:
        while not shutdown_event.is_set():
            async with settings_lock:
                eps = events_per_sec
                dup_n = duplicate_every_n
            n += 1
            if n % dup_n == 0 and last_duplicate_id:
                eid = last_duplicate_id
                log.info("sending_duplicate_eventId", event_id=eid)
            else:
                eid = str(uuid.uuid4())
                last_duplicate_id = eid
            payload = {
                "eventId": eid,
                "userId": random.choice(USER_IDS),
                "action": random.choice(ACTIONS),
            }
            carrier: dict[str, str] = {}
            inject(carrier)
            headers = kafka_headers_from_carrier(carrier)
            with tracer.start_as_current_span("produce_raw_event"):
                otel_trace.get_current_span().set_attribute("event.id", eid)
                val = encode_payload(payload)
                await producer.send(RAW_EVENTS, value=val, headers=headers)
            await asyncio.sleep(max(0.0, 1.0 / max(0.1, eps)))
    finally:
        await producer.stop()
        log.info("producer_stopped")


async def main_async() -> None:
    tasks = [
        asyncio.create_task(run_admin_server()),
        asyncio.create_task(run()),
    ]

    def stop():
        shutdown_event.set()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop)
        except NotImplementedError:
            pass

    await shutdown_event.wait()
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
