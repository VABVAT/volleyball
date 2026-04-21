"""
Mock producer: publishes synthetic raw events to `raw-events`.
Periodically reuses the same eventId to demonstrate idempotent deduplication.
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

from shared.kafka_topics import RAW_EVENTS

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

USER_IDS = [1, 2, 3, 123]
ACTIONS = ["click", "view", "purchase", "signup"]

shutdown_event = asyncio.Event()


async def run() -> None:
    producer = AIOKafkaProducer(
        bootstrap_servers=BOOTSTRAP,
        value_serializer=lambda v: json.dumps(v).encode("utf-8"),
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
            await producer.send(RAW_EVENTS, value=payload)
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
