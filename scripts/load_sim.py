#!/usr/bin/env python3
"""
Load simulation: high-rate producer into raw-events for soak testing.
Run inside the network: docker compose exec kafka bash -c '...' or from host with KAFKA_BOOTSTRAP_SERVERS=localhost:9094
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aiokafka import AIOKafkaProducer

from shared.kafka_topics import RAW_EVENTS

BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9094")
RATE = float(os.getenv("LOAD_RATE_PER_SEC", "200"))
DURATION = float(os.getenv("LOAD_DURATION_SEC", "10"))


async def main() -> None:
    producer = AIOKafkaProducer(
        bootstrap_servers=BOOTSTRAP,
        value_serializer=lambda v: json.dumps(v).encode("utf-8"),
    )
    await producer.start()
    t0 = time.time()
    n = 0
    try:
        while time.time() - t0 < DURATION:
            batch_start = time.time()
            for _ in range(max(1, int(RATE / 10))):
                payload = {
                    "eventId": str(uuid.uuid4()),
                    "userId": random.choice([1, 2, 3, 123]),
                    "action": random.choice(["click", "view", "purchase"]),
                }
                await producer.send(RAW_EVENTS, value=payload)
                n += 1
            elapsed = time.time() - batch_start
            target = 0.1
            if elapsed < target:
                await asyncio.sleep(target - elapsed)
    finally:
        await producer.stop()
    print(f"Published approximately {n} events to {RAW_EVENTS} in {DURATION}s")


if __name__ == "__main__":
    asyncio.run(main())
