"""
User Service: HTTP lookup API + publisher of user snapshots to `user-updates`.

Seeded users are pushed to Kafka on startup so the stream processor can hydrate
its local store without per-event fan-out. Ad-hoc updates use PUT and re-publish.
"""

from __future__ import annotations

import json
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Any

import structlog
from aiokafka import AIOKafkaProducer
from fastapi import FastAPI, HTTPException
from prometheus_client import Counter, Histogram, generate_latest
from pydantic_settings import BaseSettings, SettingsConfigDict
from starlette.responses import PlainTextResponse

from shared.kafka_topics import USER_UPDATES
from shared.schemas import UserProfile, UserUpdateMessage

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
        structlog.dev.ConsoleRenderer(),
    ],
)
log = structlog.get_logger()
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    kafka_bootstrap_servers: str = "localhost:9092"


settings = Settings()
producer: AIOKafkaProducer | None = None

# In-memory authoritative store for demo (could be Postgres)
USERS: dict[int, UserProfile] = {
    1: UserProfile(userId=1, name="Alice", email="alice@example.com", tier="pro"),
    2: UserProfile(userId=2, name="Bob", email="bob@example.com", tier="standard"),
    3: UserProfile(userId=3, name="Carol", email="carol@example.com", tier="standard"),
    123: UserProfile(userId=123, name="Demo", email="demo@example.com", tier="pro"),
}

HTTP_REQUESTS = Counter(
    "user_service_http_requests_total", "HTTP requests", ["method", "path", "status"]
)
HTTP_LATENCY = Histogram(
    "user_service_http_request_seconds",
    "HTTP latency",
    ["method", "path"],
)

KAFKA_PUBLISH = Counter("user_service_kafka_publish_total", "Kafka publishes to user-updates")


async def get_producer() -> AIOKafkaProducer:
    global producer
    if producer is None:
        producer = AIOKafkaProducer(
            bootstrap_servers=settings.kafka_bootstrap_servers,
            value_serializer=lambda v: json.dumps(v).encode("utf-8"),
        )
        await producer.start()
    return producer


async def publish_user_update(profile: UserProfile) -> None:
    """Publish full user snapshot to Kafka for local replicas."""
    p = await get_producer()
    body = UserUpdateMessage(user=profile).model_dump(by_alias=True)
    await p.send_and_wait(USER_UPDATES, value=body)
    KAFKA_PUBLISH.inc()
    log.info("published_user_update", user_id=profile.user_id)


async def seed_kafka() -> None:
    for uid in sorted(USERS.keys()):
        await publish_user_update(USERS[uid])


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("user_service_starting", kafka=settings.kafka_bootstrap_servers)
    await get_producer()
    await seed_kafka()
    yield
    global producer
    if producer:
        await producer.stop()
        producer = None
    log.info("user_service_stopped")


app = FastAPI(title="User Service", lifespan=lifespan)


@app.middleware("http")
async def metrics_middleware(request, call_next):
    path = request.url.path
    if path in ("/metrics", "/health", "/ready"):
        return await call_next(request)
    start = time.perf_counter()
    response = await call_next(request)
    elapsed = time.perf_counter() - start
    HTTP_LATENCY.labels(request.method, path).observe(elapsed)
    HTTP_REQUESTS.labels(request.method, path, str(response.status_code)).inc()
    return response


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/metrics")
async def metrics():
    return PlainTextResponse(generate_latest().decode("utf-8"), media_type="text/plain")


@app.get("/user/{user_id}")
async def get_user(user_id: int):
    """
    Primary read path for rare fallback when the stream processor's local store
    misses a user (cold start, replication lag, or new user).
    """
    if user_id not in USERS:
        raise HTTPException(status_code=404, detail="user not found")
    return USERS[user_id].model_dump(by_alias=True)


@app.put("/user/{user_id}")
async def upsert_user(user_id: int, body: dict[str, Any]):
    """Update user and push snapshot to Kafka so replicas converge."""
    name = str(body.get("name", f"User-{user_id}"))
    email = str(body.get("email", f"user{user_id}@example.com"))
    tier = str(body.get("tier", "standard"))
    profile = UserProfile(userId=user_id, name=name, email=email, tier=tier)
    USERS[user_id] = profile
    await publish_user_update(profile)
    return profile.model_dump(by_alias=True)


@app.post("/admin/simulate-down")
async def simulate_down():
    """
    Demo hook: remove user 123 from memory so fallback/API fails until restored.
    Used to exercise retry + DLQ without stopping the container.
    """
    USERS.pop(123, None)
    return {"ok": True, "message": "user 123 removed from memory (simulate missing user)"}


@app.post("/admin/restore-user-123")
async def restore_user_123():
    p = UserProfile(userId=123, name="Demo", email="demo@example.com", tier="pro")
    USERS[123] = p
    await publish_user_update(p)
    return {"ok": True, "user": p.model_dump(by_alias=True)}

