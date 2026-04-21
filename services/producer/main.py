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
from aiokafka import AIOKafkaProducer
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
USE_AVRO = os.getenv("KAFKA_USE_AVRO", "0").lower() in ("1", "true", "yes")

USER_IDS = [1, 2, 3, 123]
ACTIONS = ["click", "view", "purchase", "signup"]

shutdown_event = asyncio.Event()

tracer = init_tracer("producer")
_avro_parsed: dict | None = None
_raw_schema_id: int | None = None


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
            n += 1
            if n % DUPLICATE_EVERY_N == 0 and last_duplicate_id:
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
            await asyncio.sleep(INTERVAL)
    finally:
        await producer.stop()
        log.info("producer_stopped")


def main() -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def stop():
        shutdown_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop)
        except NotImplementedError:
            pass

    loop.run_until_complete(run())


if __name__ == "__main__":
    main()
