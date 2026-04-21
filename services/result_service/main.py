"""
Result Service: consumes `enriched-events` (read_committed), persists to Postgres,
exposes query API + Prometheus metrics.
"""

from __future__ import annotations

import asyncio
import os
import signal
from collections import Counter as PyCounter
from contextlib import asynccontextmanager
from typing import Any

import structlog
import uvicorn
from aiokafka import AIOKafkaConsumer
from fastapi import FastAPI
from prometheus_client import Counter, generate_latest
from sqlalchemy import Integer, String, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from starlette.responses import PlainTextResponse

from shared.kafka_topics import ENRICHED_EVENTS
from shared.message_codecs import decode_enriched
from shared.schema_registry import ensure_schema_id, load_avro_json
from shared.avro_serde import parse_schema

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.dev.ConsoleRenderer(),
    ],
)
log = structlog.get_logger()

BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://pipeline:pipeline@localhost:5432/pipeline",
)
METRICS_PORT = int(os.getenv("METRICS_PORT", "8006"))
USE_AVRO = os.getenv("KAFKA_USE_AVRO", "0").lower() in ("1", "true", "yes")

engine = create_async_engine(DATABASE_URL, echo=False)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

RECORDS_WRITTEN = Counter(
    "result_service_records_written_total",
    "Enriched events persisted",
)


class Base(DeclarativeBase):
    pass


class EnrichedRecord(Base):
    __tablename__ = "enriched_results"

    event_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(64))
    user_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    tier: Mapped[str | None] = mapped_column(String(64), nullable=True)
    enriched_at: Mapped[str] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(64))


shutdown_event = asyncio.Event()
_consume_task: asyncio.Task | None = None
_avro_parsed: dict[str, Any] | None = None


async def init_avro() -> None:
    global _avro_parsed
    if not USE_AVRO:
        return
    en_s = load_avro_json("enriched_event.avsc")
    _avro_parsed = parse_schema(en_s)
    await ensure_schema_id("enriched-events", en_s)


async def handle_enriched(msg_value: bytes) -> None:
    ev = decode_enriched(msg_value, USE_AVRO, _avro_parsed)
    u = ev.user or {}
    async with SessionLocal() as session:
        row = EnrichedRecord(
            event_id=ev.event_id,
            user_id=ev.user_id,
            action=ev.action,
            user_name=u.get("name"),
            tier=u.get("tier"),
            enriched_at=ev.enriched_at,
            source=ev.source,
        )
        await session.merge(row)
        await session.commit()
    RECORDS_WRITTEN.inc()


async def consume_loop() -> None:
    await init_avro()
    consumer = AIOKafkaConsumer(
        ENRICHED_EVENTS,
        bootstrap_servers=BOOTSTRAP,
        group_id="result-service",
        enable_auto_commit=False,
        auto_offset_reset="earliest",
        isolation_level="read_committed",
    )
    await consumer.start()
    try:
        async for msg in consumer:
            if shutdown_event.is_set():
                break
            try:
                await handle_enriched(msg.value)
            except Exception as e:
                log.exception("result_consume_failed", error=str(e))
                continue
            await consumer.commit()
    except asyncio.CancelledError:
        pass
    finally:
        await consumer.stop()


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    global _consume_task
    _consume_task = asyncio.create_task(consume_loop())
    yield
    shutdown_event.set()
    if _consume_task:
        _consume_task.cancel()
        try:
            await _consume_task
        except asyncio.CancelledError:
            pass
    await engine.dispose()


app = FastAPI(title="Result Service", lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok", "role": "result-service"}


@app.get("/metrics")
async def metrics():
    return PlainTextResponse(generate_latest().decode("utf-8"), media_type="text/plain")


@app.get("/results")
async def list_results(limit: int = 50):
    async with SessionLocal() as session:
        res = await session.execute(
            select(EnrichedRecord).order_by(EnrichedRecord.enriched_at.desc()).limit(limit)
        )
        rows = res.scalars().all()
    return [
        {
            "event_id": r.event_id,
            "user_id": r.user_id,
            "action": r.action,
            "user_name": r.user_name,
            "tier": r.tier,
            "enriched_at": r.enriched_at,
            "source": r.source,
        }
        for r in rows
    ]


@app.get("/results/stats")
async def stats():
    async with SessionLocal() as session:
        res = await session.execute(select(EnrichedRecord))
        rows = res.scalars().all()
    by_action = PyCounter(r.action for r in rows)
    by_tier = PyCounter((r.tier or "unknown") for r in rows)
    by_source = PyCounter(r.source for r in rows)
    return {
        "total": len(rows),
        "by_action": dict(by_action),
        "by_tier": dict(by_tier),
        "by_source": dict(by_source),
    }


async def main() -> None:
    config = uvicorn.Config(app, host="0.0.0.0", port=METRICS_PORT, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()


if __name__ == "__main__":
    asyncio.run(main())
