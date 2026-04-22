"""Dashboard API — metrics aggregation + scenario proxies."""
from __future__ import annotations

import asyncio
import json
import os
import time
from contextlib import asynccontextmanager
from typing import Any

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Path, Query
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse

from dashboard_api.scraper import (
    MetricsStore,
    ServiceSnapshot,
    Snapshot,
    parse_prometheus_text,
)
from dashboard_api.activity import append_activity, get_activity
from dashboard_api.scenarios import (
    delete_demo_user,
    load_burst,
    replay_dlq,
    restore_demo_user,
)

STREAM = os.getenv("METRICS_STREAM_PROCESSOR", "http://localhost:8002")
RETRY_SVC = os.getenv("METRICS_RETRY_WORKER", "http://localhost:8004")
DLQ_SVC = os.getenv("METRICS_DLQ_HANDLER", "http://localhost:8005")
USER_METRICS = os.getenv("METRICS_USER_SERVICE", "http://localhost:8080")
RESULT = os.getenv("RESULT_SERVICE_URL", "http://localhost:8006")
USER_SERVICE_URL = os.getenv("USER_SERVICE_URL", "http://localhost:8080")
PRODUCER_ADMIN_URL = os.getenv("PRODUCER_ADMIN_URL", "http://localhost:8010")
KAFKA = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
SCRAPE_INTERVAL = float(os.getenv("SCRAPE_INTERVAL_SEC", "2.0"))

_DEMO_USER_IDS = frozenset({1, 2, 3, 123})

store = MetricsStore(maxlen=300)


async def _scrape(client: httpx.AsyncClient, url: str) -> ServiceSnapshot:
    try:
        r = await client.get(f"{url.rstrip('/')}/metrics", timeout=3.0)
        r.raise_for_status()
        return ServiceSnapshot(raw=parse_prometheus_text(r.text), reachable=True)
    except Exception:
        return ServiceSnapshot(raw={}, reachable=False)


async def _scraper_loop() -> None:
    async with httpx.AsyncClient() as client:
        while True:
            t0 = time.time()
            sp, rw, dq, us, rs = await asyncio.gather(
                _scrape(client, STREAM),
                _scrape(client, RETRY_SVC),
                _scrape(client, DLQ_SVC),
                _scrape(client, USER_METRICS),
                _scrape(client, RESULT),
            )
            snap = Snapshot(ts=t0, sp=sp, rw=rw, dq=dq, us=us, rs=rs)
            store.push(snap)
            prev = store.prev()
            if prev is not None and snap.sp.reachable and prev.sp.reachable:
                dt = snap.ts - prev.ts
                if dt > 0:
                    key_raw = "stream_processor_events_consumed_total"
                    key_en = "stream_processor_enriched_events_total"
                    r0 = float(prev.sp.raw.get(key_raw) or 0.0)
                    r1 = float(snap.sp.raw.get(key_raw) or 0.0)
                    e0 = float(prev.sp.raw.get(key_en) or 0.0)
                    e1 = float(snap.sp.raw.get(key_en) or 0.0)
                    dr = int(r1 - r0)
                    de = int(e1 - e0)
                    await append_activity(
                        f"pipeline tick Δ{dt:.1f}s  raw_consumed +{dr}  enriched +{de}"
                    )
            await asyncio.sleep(max(0.1, SCRAPE_INTERVAL - (time.time() - t0)))


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(_scraper_loop())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="Dashboard API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)


def _snap_to_dict(s: Snapshot) -> dict[str, Any]:
    return {
        "ts": s.ts,
        "sp": {"raw": s.sp.raw, "reachable": s.sp.reachable},
        "rw": {"raw": s.rw.raw, "reachable": s.rw.reachable},
        "dq": {"raw": s.dq.raw, "reachable": s.dq.reachable},
        "us": {"raw": s.us.raw, "reachable": s.us.reachable},
        "rs": {"raw": s.rs.raw, "reachable": s.rs.reachable},
    }


def _derived(latest: Snapshot, prev: Snapshot | None) -> dict[str, Any]:
    sp = latest.sp.raw
    rw = latest.rw.raw

    enriched = sp.get("stream_processor_enriched_events_total", 0.0)
    retry_out = sp.get("stream_processor_retry_published_total", 0.0)
    total_out = enriched + retry_out
    success_rate = (enriched / total_out * 100) if total_out > 0 else None

    lat_sum = sp.get("stream_processor_processing_seconds_sum", 0.0)
    lat_count = sp.get("stream_processor_processing_seconds_count", 0.0)
    avg_latency_ms = (lat_sum / lat_count * 1000) if lat_count > 0 else None

    retry_consumed = rw.get("retry_worker_messages_consumed_total", 0.0)
    retry_success = rw.get("retry_worker_success_total", 0.0)
    retry_dlq = rw.get("retry_worker_dlq_published_total", 0.0)
    retry_backlog = max(0.0, retry_consumed - retry_success - retry_dlq)

    throughput_eps: float | None = None
    if prev is not None and prev.sp.reachable and latest.sp.reachable:
        dt = latest.ts - prev.ts
        if dt > 0:
            prev_enriched = prev.sp.raw.get("stream_processor_enriched_events_total", 0.0)
            throughput_eps = max(0.0, (enriched - prev_enriched) / dt)

    return {
        "throughput_eps": throughput_eps,
        "success_rate_pct": success_rate,
        "avg_latency_ms": avg_latency_ms,
        "retry_backlog": retry_backlog,
    }


@app.get("/api/metrics/current")
async def current_metrics():
    latest = store.latest()
    if latest is None:
        return JSONResponse({"error": "no data yet"}, status_code=503)
    return {"snapshot": _snap_to_dict(latest), "derived": _derived(latest, store.prev())}


@app.get("/api/metrics/timeseries")
async def timeseries(window: int = Query(300, ge=10, le=600)):
    return [_snap_to_dict(s) for s in store.since(window)]


@app.get("/api/health")
async def health():
    latest = store.latest()
    if latest is None:
        return {
            k: False
            for k in (
                "stream_processor",
                "retry_worker",
                "dlq_handler",
                "user_service",
                "result_service",
            )
        }
    return {
        "stream_processor": latest.sp.reachable,
        "retry_worker": latest.rw.reachable,
        "dlq_handler": latest.dq.reachable,
        "user_service": latest.us.reachable,
        "result_service": latest.rs.reachable,
    }


@app.get("/api/results")
async def proxy_results(limit: int = 50):
    async with httpx.AsyncClient(timeout=5.0) as client:
        r = await client.get(f"{RESULT.rstrip('/')}/results", params={"limit": limit})
        return r.json()


@app.get("/api/results/stats")
async def proxy_stats():
    async with httpx.AsyncClient(timeout=5.0) as client:
        r = await client.get(f"{RESULT.rstrip('/')}/results/stats")
        return r.json()


@app.get("/api/activity")
async def activity_feed(limit: int = Query(150, ge=10, le=500)):
    return {"lines": await get_activity(limit)}


@app.get("/api/users/summary")
async def users_summary_feed():
    async with httpx.AsyncClient(timeout=5.0) as client:
        r = await client.get(f"{USER_SERVICE_URL.rstrip('/')}/admin/users-summary")
        r.raise_for_status()
        return r.json()


@app.post("/api/scenarios/demo-users/{user_id}/delete")
async def scenario_delete_demo_user(user_id: int = Path(..., ge=1)):
    if user_id not in _DEMO_USER_IDS:
        raise HTTPException(status_code=400, detail="user_id must be 1, 2, 3, or 123")
    try:
        out = await delete_demo_user(USER_SERVICE_URL, user_id)
        await append_activity(f"delete demo user {user_id} → {json.dumps(out, default=str)[:500]}")
        return out
    except Exception as e:
        await append_activity(f"delete demo user {user_id} ERROR: {e!s}")
        raise


@app.post("/api/scenarios/demo-users/{user_id}/restore")
async def scenario_restore_demo_user(user_id: int = Path(..., ge=1)):
    if user_id not in _DEMO_USER_IDS:
        raise HTTPException(status_code=400, detail="user_id must be 1, 2, 3, or 123")
    try:
        out = await restore_demo_user(USER_SERVICE_URL, user_id)
        await append_activity(f"restore demo user {user_id} → {json.dumps(out, default=str)[:500]}")
        return out
    except Exception as e:
        await append_activity(f"restore demo user {user_id} ERROR: {e!s}")
        raise


@app.post("/api/scenarios/load-burst")
async def scenario_load_burst(rate: int = 200, duration: int = 10):
    try:
        out = await load_burst(KAFKA, rate, duration)
        await append_activity(f"load-burst rate={rate} duration={duration} → {json.dumps(out, default=str)[:500]}")
        return out
    except Exception as e:
        await append_activity(f"load-burst ERROR: {e!s}")
        raise


@app.post("/api/scenarios/replay-dlq")
async def scenario_replay_dlq(limit: int = 100):
    try:
        out = await replay_dlq(DLQ_SVC, limit)
        await append_activity(f"replay-dlq limit={limit} → {json.dumps(out, default=str)[:500]}")
        return out
    except Exception as e:
        await append_activity(f"replay-dlq ERROR: {e!s}")
        raise


@app.get("/api/health/self")
async def self_health():
    return {"status": "ok"}


@app.get("/api/controls/producer")
async def get_producer_controls():
    async with httpx.AsyncClient(timeout=3.0) as client:
        speed = await client.get(f"{PRODUCER_ADMIN_URL.rstrip('/')}/admin/speed")
        dups = await client.get(f"{PRODUCER_ADMIN_URL.rstrip('/')}/admin/duplicates")
        return {"speed": speed.json(), "duplicates": dups.json()}


@app.post("/api/controls/producer/speed")
async def set_producer_speed(eps: float = Query(..., gt=0.0, le=5000.0)):
    async with httpx.AsyncClient(timeout=3.0) as client:
        r = await client.post(f"{PRODUCER_ADMIN_URL.rstrip('/')}/admin/speed", params={"eps": eps})
        r.raise_for_status()
        out = r.json()
    await append_activity(f"producer speed eps={eps} → {json.dumps(out, default=str)[:300]}")
    return out


@app.post("/api/controls/producer/duplicates")
async def set_producer_duplicates(every_n: int = Query(..., ge=1, le=1_000_000)):
    async with httpx.AsyncClient(timeout=3.0) as client:
        r = await client.post(
            f"{PRODUCER_ADMIN_URL.rstrip('/')}/admin/duplicates",
            params={"every_n": every_n},
        )
        r.raise_for_status()
        out = r.json()
    await append_activity(f"producer duplicates every_n={every_n} → {json.dumps(out, default=str)[:300]}")
    return out


if __name__ == "__main__":
    uvicorn.run("dashboard_api.main:app", host="0.0.0.0", port=8090, reload=False)

