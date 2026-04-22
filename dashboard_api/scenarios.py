"""Scenario trigger handlers for the dashboard."""
from __future__ import annotations

import asyncio
import json
import random
import uuid

import httpx
from aiokafka import AIOKafkaProducer


async def simulate_down(user_service_url: str) -> dict:
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.post(f"{user_service_url.rstrip('/')}/admin/simulate-down")
        r.raise_for_status()
        return r.json()


async def restore_user(user_service_url: str) -> dict:
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.post(f"{user_service_url.rstrip('/')}/admin/restore-user-123")
        r.raise_for_status()
        return r.json()


async def random_user_deletions(user_service_url: str, count: int) -> dict:
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.post(
            f"{user_service_url.rstrip('/')}/admin/simulate-random-deletions",
            params={"count": count},
        )
        r.raise_for_status()
        return r.json()


async def restore_random_deletions(user_service_url: str) -> dict:
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.post(f"{user_service_url.rstrip('/')}/admin/restore-random-deletions")
        r.raise_for_status()
        return r.json()


async def replay_dlq(dlq_url: str, limit: int = 100) -> dict:
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(f"{dlq_url.rstrip('/')}/replay", params={"limit": limit})
        r.raise_for_status()
        return r.json()


async def load_burst(bootstrap: str, rate: int = 200, duration_sec: int = 10) -> dict:
    """Publish `rate` events/sec to raw-events for `duration_sec` seconds."""
    producer = AIOKafkaProducer(
        bootstrap_servers=bootstrap,
        value_serializer=lambda v: json.dumps(v).encode("utf-8"),
    )
    await producer.start()
    n = 0
    loop = asyncio.get_event_loop()
    t0 = loop.time()
    interval = 1.0 / rate
    try:
        while loop.time() - t0 < duration_sec:
            payload = {
                "eventId": str(uuid.uuid4()),
                "userId": random.choice(list(range(1, 31)) + [123]),
                "action": random.choice(["click", "view", "purchase", "signup"]),
            }
            await producer.send("raw-events", value=payload)
            n += 1
            await asyncio.sleep(interval)
    finally:
        await producer.stop()
    return {"published": n, "duration_sec": duration_sec}

