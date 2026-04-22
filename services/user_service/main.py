"""
User Service: HTTP lookup API + publisher of user snapshots to `user-updates`.
Users are stored in PostgreSQL (async SQLAlchemy); seeded on first boot.
"""

from __future__ import annotations

import json
import logging
import os
import random
import time
from contextlib import asynccontextmanager
from typing import Any

import structlog
from aiokafka import AIOKafkaProducer
from fastapi import FastAPI, HTTPException
from prometheus_client import Counter, Histogram, generate_latest
from pydantic_settings import BaseSettings, SettingsConfigDict
from redis.asyncio import Redis
from sqlalchemy import Integer, String, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from starlette.responses import PlainTextResponse

from shared.kafka_topics import USER_UPDATES
from shared.redis_keys import user_profile_key
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
    database_url: str = "postgresql+asyncpg://pipeline:pipeline@localhost:5432/pipeline"


settings = Settings()
engine = create_async_engine(settings.database_url, echo=False)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


class Base(DeclarativeBase):
    pass


class UserRow(Base):
    __tablename__ = "users"

    user_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    email: Mapped[str] = mapped_column(String(255))
    tier: Mapped[str] = mapped_column(String(64), default="standard")


producer: AIOKafkaProducer | None = None
_redis_client: Redis | None = None

# Last batch removed by /admin/simulate-random-deletions (for restore).
last_random_deleted_user_ids: list[int] = []


def _build_canonical_seed_rows() -> list[UserRow]:
    """31 users: ids 1–30 plus demo user 123 (matches producer pool)."""
    rows: list[UserRow] = []
    for uid in range(1, 31):
        tier = "pro" if uid % 7 == 0 else "standard"
        rows.append(UserRow(user_id=uid, name=f"User-{uid}", email=f"user{uid}@example.com", tier=tier))
    rows.append(UserRow(user_id=123, name="Demo", email="demo@example.com", tier="pro"))
    return rows


CANONICAL_USERS: dict[int, UserRow] = {r.user_id: r for r in _build_canonical_seed_rows()}

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


def row_to_profile(row: UserRow) -> UserProfile:
    return UserProfile(userId=row.user_id, name=row.name, email=row.email, tier=row.tier)


async def _get_redis_if_configured() -> Redis | None:
    global _redis_client
    url = os.getenv("REDIS_URL", "").strip()
    if not url:
        return None
    if _redis_client is None:
        _redis_client = Redis.from_url(url, decode_responses=True)
    return _redis_client


async def _invalidate_redis_user_projection(user_id: int) -> None:
    """Drop cached profile so stream-processor/retry-worker re-fetch from HTTP after simulate-down."""
    client = await _get_redis_if_configured()
    if client is None:
        return
    try:
        await client.delete(user_profile_key(user_id))
        log.info("redis_user_projection_deleted", user_id=user_id)
    except Exception as e:
        log.warning("redis_user_projection_delete_failed", user_id=user_id, error=str(e))


async def publish_user_update(profile: UserProfile) -> None:
    p = await get_producer()
    body = UserUpdateMessage(user=profile).model_dump(by_alias=True)
    await p.send_and_wait(USER_UPDATES, value=body)
    KAFKA_PUBLISH.inc()
    log.info("published_user_update", user_id=profile.user_id)


async def seed_db_and_kafka() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with SessionLocal() as session:
        exists = (await session.execute(select(UserRow).limit(1))).first()
        if exists is None:
            seed = [
                UserRow(
                    user_id=uid,
                    name=CANONICAL_USERS[uid].name,
                    email=CANONICAL_USERS[uid].email,
                    tier=CANONICAL_USERS[uid].tier,
                )
                for uid in sorted(CANONICAL_USERS.keys())
            ]
            session.add_all(seed)
            await session.commit()
            log.info("seeded_users", count=len(seed))

    async with SessionLocal() as session:
        result = await session.execute(select(UserRow).order_by(UserRow.user_id))
        rows = result.scalars().all()
        for row in rows:
            await publish_user_update(row_to_profile(row))


async def ensure_minimum_seed_users() -> None:
    """Upsert missing canonical users (e.g. DB created before we expanded to 30+ users)."""
    added: list[int] = []
    async with SessionLocal() as session:
        for uid in sorted(CANONICAL_USERS.keys()):
            tmpl = CANONICAL_USERS[uid]
            row = await session.get(UserRow, uid)
            if row is None:
                session.add(UserRow(user_id=uid, name=tmpl.name, email=tmpl.email, tier=tmpl.tier))
                added.append(uid)
        await session.commit()
    for uid in added:
        async with SessionLocal() as session:
            row = await session.get(UserRow, uid)
            if row is not None:
                await publish_user_update(row_to_profile(row))
    if added:
        log.info("ensure_seed_users_added", count=len(added), user_ids=added)


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("user_service_starting", kafka=settings.kafka_bootstrap_servers)
    await get_producer()
    await seed_db_and_kafka()
    await ensure_minimum_seed_users()
    yield
    global producer, _redis_client
    if producer:
        await producer.stop()
        producer = None
    if _redis_client is not None:
        await _redis_client.close()
        _redis_client = None
    await engine.dispose()
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
    async with SessionLocal() as session:
        row = await session.get(UserRow, user_id)
        if row is None:
            raise HTTPException(status_code=404, detail="user not found")
        return row_to_profile(row).model_dump(by_alias=True)


@app.put("/user/{user_id}")
async def upsert_user(user_id: int, body: dict[str, Any]):
    name = str(body.get("name", f"User-{user_id}"))
    email = str(body.get("email", f"user{user_id}@example.com"))
    tier = str(body.get("tier", "standard"))
    async with SessionLocal() as session:
        row = await session.get(UserRow, user_id)
        if row is None:
            row = UserRow(user_id=user_id, name=name, email=email, tier=tier)
            session.add(row)
        else:
            row.name = name
            row.email = email
            row.tier = tier
        await session.commit()
        profile = row_to_profile(row)
    await publish_user_update(profile)
    return profile.model_dump(by_alias=True)


@app.get("/admin/users-summary")
async def users_summary():
    async with SessionLocal() as session:
        n = (await session.execute(select(func.count()).select_from(UserRow))).scalar_one()
    return {"user_count": int(n), "canonical_total": len(CANONICAL_USERS)}


@app.post("/admin/simulate-random-deletions")
async def simulate_random_deletions(count: int = Query(3, ge=1, le=5)):
    """Delete up to `count` random users while keeping at least 25 rows (so ≥25 remain)."""
    global last_random_deleted_user_ids
    async with SessionLocal() as session:
        r = await session.execute(select(UserRow.user_id))
        ids = list(r.scalars().all())
    if not ids:
        return {"ok": True, "deleted_user_ids": [], "message": "no users in database"}
    cap = max(0, len(ids) - 25)
    if cap < 1:
        return {
            "ok": True,
            "deleted_user_ids": [],
            "message": f"would drop below 25 users (have {len(ids)}); skip",
        }
    n = min(count, cap, 5)
    picked = random.sample(ids, n)
    async with SessionLocal() as session:
        for uid in picked:
            row = await session.get(UserRow, uid)
            if row is not None:
                await session.delete(row)
        await session.commit()
    for uid in picked:
        await _invalidate_redis_user_projection(uid)
    last_random_deleted_user_ids = list(picked)
    log.info("random_user_deletions", user_ids=picked)
    return {"ok": True, "deleted_user_ids": picked, "count": len(picked)}


@app.post("/admin/restore-random-deletions")
async def restore_random_deletions():
    """Re-upsert users removed by the last simulate-random-deletions call."""
    global last_random_deleted_user_ids
    if not last_random_deleted_user_ids:
        return {"ok": True, "restored_user_ids": [], "message": "nothing to restore"}
    restored: list[int] = []
    async with SessionLocal() as session:
        for uid in last_random_deleted_user_ids:
            tmpl = CANONICAL_USERS.get(uid)
            if tmpl is None:
                continue
            row = await session.get(UserRow, uid)
            if row is None:
                session.add(
                    UserRow(user_id=uid, name=tmpl.name, email=tmpl.email, tier=tmpl.tier),
                )
            else:
                row.name = tmpl.name
                row.email = tmpl.email
                row.tier = tmpl.tier
            restored.append(uid)
        await session.commit()
    for uid in restored:
        async with SessionLocal() as session:
            row = await session.get(UserRow, uid)
            if row is not None:
                await publish_user_update(row_to_profile(row))
    last_random_deleted_user_ids = []
    log.info("restore_random_deletions", user_ids=restored)
    return {"ok": True, "restored_user_ids": restored, "count": len(restored)}


@app.post("/admin/simulate-down")
async def simulate_down():
    async with SessionLocal() as session:
        row = await session.get(UserRow, 123)
        if row:
            await session.delete(row)
            await session.commit()
    await _invalidate_redis_user_projection(123)
    return {"ok": True, "message": "user 123 removed from database (simulate missing user)"}


@app.post("/admin/restore-user-123")
async def restore_user_123():
    async with SessionLocal() as session:
        row = await session.get(UserRow, 123)
        if row is None:
            row = UserRow(user_id=123, name="Demo", email="demo@example.com", tier="pro")
            session.add(row)
        else:
            row.name = "Demo"
            row.email = "demo@example.com"
            row.tier = "pro"
        await session.commit()
        profile = row_to_profile(row)
    await publish_user_update(profile)
    return {"ok": True, "user": profile.model_dump(by_alias=True)}
