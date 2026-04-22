# Dashboard Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Streamlit prototype with a React + FastAPI dashboard that shows live time-series charts, consumer lag, retry/DLQ health, and a scenario control panel.

**Architecture:** A new `dashboard-api` FastAPI service scrapes all five service Prometheus `/metrics` endpoints every 2 s, stores a 10-min rolling time series in memory, and exposes clean JSON endpoints. A `dashboard-ui` Vite/React SPA polls those endpoints every 3 s and renders charts with Recharts. Nginx serves the static build and proxies `/api` to `dashboard-api`. The old Streamlit `dashboard` service is replaced by both.

**Tech Stack:** Python 3.12 FastAPI (backend), Vite + React 18 + TypeScript + Tailwind CSS + Recharts (frontend), Nginx (static server + proxy), Docker Compose (two new services).

---

## File Map

```
dashboard_api/
  __init__.py            new — package marker
  scraper.py             new — Prometheus text parser + MetricsStore
  scenarios.py           new — simulate-down, restore-user, load-burst, replay-dlq
  main.py                new — FastAPI app, background scraper, all endpoints
  requirements.txt       new — minimal deps (fastapi, uvicorn, httpx, aiokafka)
  Dockerfile             new

dashboard_ui/
  package.json           new
  vite.config.ts         new
  tsconfig.json          new
  tailwind.config.ts     new
  postcss.config.js      new
  index.html             new
  nginx.conf             new
  Dockerfile             new
  src/
    main.tsx             new — React entry point
    App.tsx              new — Router + NavBar shell
    api/
      types.ts           new — TypeScript interfaces
      client.ts          new — fetch wrappers for all endpoints
    hooks/
      useCurrentMetrics.ts  new — polls /api/metrics/current every 2 s
      useTimeSeries.ts      new — polls /api/metrics/timeseries every 3 s
      useHealth.ts          new — polls /api/health every 5 s
      useResults.ts         new — polls /api/results + /api/results/stats every 5 s
    components/
      NavBar.tsx            new
      ServiceHealthBar.tsx  new
      KpiCard.tsx           new
      ThroughputChart.tsx   new — recharts LineChart
      ConsumerLagChart.tsx  new — recharts BarChart
      LatencyHistogram.tsx  new — recharts BarChart
      EnrichmentFunnel.tsx  new — recharts BarChart
      ScenarioPanel.tsx     new — buttons + activity log
      ResultsTable.tsx      new
    pages/
      Overview.tsx          new
      Pipeline.tsx          new
      FaultTolerance.tsx    new
      Scenarios.tsx         new
      Results.tsx           new

tests/
  dashboard_api/
    test_scraper.py      new — unit tests for parser + store

infra/docker-compose.yml   modify — remove `dashboard`, add `dashboard-api` + `dashboard-ui`
```

---

## Task 1: Prometheus Parser + MetricsStore

**Files:**
- Create: `dashboard_api/__init__.py`
- Create: `dashboard_api/scraper.py`
- Create: `tests/dashboard_api/__init__.py`
- Create: `tests/dashboard_api/test_scraper.py`

- [ ] **Step 1: Create the package markers**

```bash
touch /home/ashwin/volleyball/volleyball/dashboard_api/__init__.py
mkdir -p /home/ashwin/volleyball/volleyball/tests/dashboard_api
touch /home/ashwin/volleyball/volleyball/tests/__init__.py
touch /home/ashwin/volleyball/volleyball/tests/dashboard_api/__init__.py
```

- [ ] **Step 2: Write failing tests first**

Create `tests/dashboard_api/test_scraper.py`:

```python
"""Unit tests for Prometheus parser and MetricsStore."""
import time
import pytest
from dashboard_api.scraper import (
    MetricsStore,
    ServiceSnapshot,
    Snapshot,
    parse_prometheus_text,
)

SAMPLE_PROMETHEUS = """\
# HELP stream_processor_events_consumed_total Raw events consumed
# TYPE stream_processor_events_consumed_total counter
stream_processor_events_consumed_total 42.0
# HELP stream_processor_consumer_lag_messages Consumer lag
# TYPE stream_processor_consumer_lag_messages gauge
stream_processor_consumer_lag_messages{partition="0",topic="raw-events"} 5.0
stream_processor_consumer_lag_messages{partition="1",topic="raw-events"} 3.0
stream_processor_processing_seconds_sum 1.234
stream_processor_processing_seconds_count 100.0
"""


def test_parse_simple_counter():
    result = parse_prometheus_text(SAMPLE_PROMETHEUS)
    assert result["stream_processor_events_consumed_total"] == 42.0


def test_parse_labeled_gauge():
    result = parse_prometheus_text(SAMPLE_PROMETHEUS)
    key = 'stream_processor_consumer_lag_messages{partition="0",topic="raw-events"}'
    assert result[key] == 5.0


def test_parse_histogram_fields():
    result = parse_prometheus_text(SAMPLE_PROMETHEUS)
    assert result["stream_processor_processing_seconds_sum"] == pytest.approx(1.234)
    assert result["stream_processor_processing_seconds_count"] == 100.0


def test_parse_skips_comments_and_blanks():
    result = parse_prometheus_text("# HELP foo bar\n\nfoo 1.0\n")
    assert list(result.keys()) == ["foo"]


def test_parse_invalid_value_skipped():
    result = parse_prometheus_text("foo NaN\nbar 2.0\n")
    assert "foo" not in result
    assert result["bar"] == 2.0


def _make_snap(ts: float, enriched: float = 0.0, reachable: bool = True) -> Snapshot:
    raw = {"stream_processor_enriched_events_total": enriched}
    sp = ServiceSnapshot(raw=raw, reachable=reachable)
    empty = ServiceSnapshot(raw={}, reachable=True)
    return Snapshot(ts=ts, sp=sp, rw=empty, dq=empty, us=empty, rs=empty)


def test_store_latest_empty():
    store = MetricsStore()
    assert store.latest() is None
    assert store.prev() is None


def test_store_push_and_latest():
    store = MetricsStore()
    s = _make_snap(1000.0, enriched=10.0)
    store.push(s)
    assert store.latest() is s


def test_store_prev_after_two_pushes():
    store = MetricsStore()
    s1 = _make_snap(1000.0)
    s2 = _make_snap(1002.0)
    store.push(s1)
    store.push(s2)
    assert store.prev() is s1
    assert store.latest() is s2


def test_store_since_filters_by_window():
    store = MetricsStore()
    now = time.time()
    store.push(_make_snap(now - 400))
    store.push(_make_snap(now - 200))
    store.push(_make_snap(now - 50))
    result = store.since(300)
    assert len(result) == 2
    assert all(s.ts >= now - 300 for s in result)


def test_store_maxlen_evicts_oldest():
    store = MetricsStore(maxlen=3)
    for i in range(5):
        store.push(_make_snap(float(i)))
    snaps = store.all()
    assert len(snaps) == 3
    assert snaps[0].ts == 2.0
```

- [ ] **Step 3: Run tests — expect ImportError (module doesn't exist yet)**

```bash
cd /home/ashwin/volleyball/volleyball
python -m pytest tests/dashboard_api/test_scraper.py -v 2>&1 | head -20
```

Expected: `ModuleNotFoundError: No module named 'dashboard_api'`

- [ ] **Step 4: Create `dashboard_api/scraper.py`**

```python
"""Prometheus metrics scraper and rolling time-series store."""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field


def parse_prometheus_text(text: str) -> dict[str, float]:
    result: dict[str, float] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.rsplit(" ", 1)
        if len(parts) != 2:
            continue
        key, val_str = parts
        try:
            result[key.strip()] = float(val_str.strip())
        except ValueError:
            pass
    return result


@dataclass
class ServiceSnapshot:
    raw: dict[str, float] = field(default_factory=dict)
    reachable: bool = True


@dataclass
class Snapshot:
    ts: float
    sp: ServiceSnapshot
    rw: ServiceSnapshot
    dq: ServiceSnapshot
    us: ServiceSnapshot
    rs: ServiceSnapshot


class MetricsStore:
    def __init__(self, maxlen: int = 300) -> None:
        self._series: deque[Snapshot] = deque(maxlen=maxlen)

    def push(self, snapshot: Snapshot) -> None:
        self._series.append(snapshot)

    def latest(self) -> Snapshot | None:
        return self._series[-1] if self._series else None

    def prev(self) -> Snapshot | None:
        return self._series[-2] if len(self._series) >= 2 else None

    def since(self, window_sec: float) -> list[Snapshot]:
        cutoff = time.time() - window_sec
        return [s for s in self._series if s.ts >= cutoff]

    def all(self) -> list[Snapshot]:
        return list(self._series)
```

- [ ] **Step 5: Run tests — expect all pass**

```bash
cd /home/ashwin/volleyball/volleyball
python -m pytest tests/dashboard_api/test_scraper.py -v
```

Expected output: `11 passed`

- [ ] **Step 6: Commit**

```bash
cd /home/ashwin/volleyball/volleyball
git add dashboard_api/__init__.py dashboard_api/scraper.py tests/
git commit -m "feat(dashboard): add Prometheus parser and MetricsStore with tests"
```

---

## Task 2: Scenario Handlers

**Files:**
- Create: `dashboard_api/scenarios.py`

- [ ] **Step 1: Create `dashboard_api/scenarios.py`**

```python
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


async def replay_dlq(dlq_url: str, limit: int = 100) -> dict:
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(
            f"{dlq_url.rstrip('/')}/replay", params={"limit": limit}
        )
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
                "userId": random.choice([1, 2, 3, 123]),
                "action": random.choice(["click", "view", "purchase", "signup"]),
            }
            await producer.send("raw-events", value=payload)
            n += 1
            await asyncio.sleep(interval)
    finally:
        await producer.stop()
    return {"published": n, "duration_sec": duration_sec}
```

- [ ] **Step 2: Commit**

```bash
cd /home/ashwin/volleyball/volleyball
git add dashboard_api/scenarios.py
git commit -m "feat(dashboard): add scenario handlers (simulate-down, load-burst, replay-dlq)"
```

---

## Task 3: FastAPI Dashboard API

**Files:**
- Create: `dashboard_api/main.py`
- Create: `dashboard_api/requirements.txt`

- [ ] **Step 1: Create `dashboard_api/requirements.txt`**

```
fastapi==0.115.6
uvicorn[standard]==0.32.1
httpx==0.28.1
aiokafka==0.11.0
pydantic-settings==2.6.1
```

- [ ] **Step 2: Create `dashboard_api/main.py`**

```python
"""Dashboard API — metrics aggregation + scenario proxies."""
from __future__ import annotations

import asyncio
import os
import time
from contextlib import asynccontextmanager
from typing import Any

import httpx
import uvicorn
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse

from dashboard_api.scraper import (
    MetricsStore,
    ServiceSnapshot,
    Snapshot,
    parse_prometheus_text,
)
from dashboard_api.scenarios import load_burst, replay_dlq, restore_user, simulate_down

STREAM = os.getenv("METRICS_STREAM_PROCESSOR", "http://localhost:8002")
RETRY_SVC = os.getenv("METRICS_RETRY_WORKER", "http://localhost:8004")
DLQ_SVC = os.getenv("METRICS_DLQ_HANDLER", "http://localhost:8005")
USER_METRICS = os.getenv("METRICS_USER_SERVICE", "http://localhost:8080")
RESULT = os.getenv("RESULT_SERVICE_URL", "http://localhost:8006")
USER_SERVICE_URL = os.getenv("USER_SERVICE_URL", "http://localhost:8080")
KAFKA = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
SCRAPE_INTERVAL = float(os.getenv("SCRAPE_INTERVAL_SEC", "2.0"))

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
            store.push(Snapshot(ts=t0, sp=sp, rw=rw, dq=dq, us=us, rs=rs))
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
        return {k: False for k in ("stream_processor", "retry_worker", "dlq_handler", "user_service", "result_service")}
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


@app.post("/api/scenarios/simulate-down")
async def scenario_simulate_down():
    return await simulate_down(USER_SERVICE_URL)


@app.post("/api/scenarios/restore-user")
async def scenario_restore_user():
    return await restore_user(USER_SERVICE_URL)


@app.post("/api/scenarios/load-burst")
async def scenario_load_burst(rate: int = 200, duration: int = 10):
    return await load_burst(KAFKA, rate, duration)


@app.post("/api/scenarios/replay-dlq")
async def scenario_replay_dlq(limit: int = 100):
    return await replay_dlq(DLQ_SVC, limit)


@app.get("/api/health/self")
async def self_health():
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run("dashboard_api.main:app", host="0.0.0.0", port=8090, reload=False)
```

- [ ] **Step 3: Verify the module imports cleanly**

```bash
cd /home/ashwin/volleyball/volleyball
pip install fastapi uvicorn httpx aiokafka --quiet
python -c "from dashboard_api.main import app; print('OK')"
```

Expected: `OK`

- [ ] **Step 4: Commit**

```bash
cd /home/ashwin/volleyball/volleyball
git add dashboard_api/main.py dashboard_api/requirements.txt
git commit -m "feat(dashboard): add FastAPI aggregation API with metrics, health, and scenario endpoints"
```

---

## Task 4: Backend Dockerfile + Docker Compose Update

**Files:**
- Create: `dashboard_api/Dockerfile`
- Modify: `infra/docker-compose.yml`

- [ ] **Step 1: Create `dashboard_api/Dockerfile`**

```dockerfile
FROM python:3.12-slim

WORKDIR /app

COPY dashboard_api/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY dashboard_api /app/dashboard_api

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

CMD ["python", "-m", "uvicorn", "dashboard_api.main:app", "--host", "0.0.0.0", "--port", "8090"]
```

- [ ] **Step 2: Update `infra/docker-compose.yml`**

Remove the entire `dashboard:` block. Then add these two new services before the `volumes:` section:

```yaml
  dashboard-api:
    build:
      context: ..
      dockerfile: dashboard_api/Dockerfile
    depends_on:
      - stream-processor
      - retry-worker
      - dlq-handler
      - user-service
      - result-service
    environment:
      METRICS_STREAM_PROCESSOR: http://stream-processor:8002
      METRICS_RETRY_WORKER: http://retry-worker:8004
      METRICS_DLQ_HANDLER: http://dlq-handler:8005
      METRICS_USER_SERVICE: http://user-service:8080
      RESULT_SERVICE_URL: http://result-service:8006
      USER_SERVICE_URL: http://user-service:8080
      KAFKA_BOOTSTRAP_SERVERS: kafka:9092
    ports:
      - "8090:8090"

  dashboard-ui:
    build:
      context: ..
      dockerfile: dashboard_ui/Dockerfile
    depends_on:
      - dashboard-api
    ports:
      - "8501:80"
```

- [ ] **Step 3: Verify docker-compose parses correctly**

```bash
cd /home/ashwin/volleyball/volleyball
docker compose -f infra/docker-compose.yml config --quiet && echo "YAML valid"
```

Expected: `YAML valid`

- [ ] **Step 4: Commit**

```bash
cd /home/ashwin/volleyball/volleyball
git add dashboard_api/Dockerfile infra/docker-compose.yml
git commit -m "feat(dashboard): add dashboard-api Dockerfile and docker-compose services"
```

---

## Task 5: React Project Scaffold

**Files:**
- Create: `dashboard_ui/package.json`
- Create: `dashboard_ui/tsconfig.json`
- Create: `dashboard_ui/vite.config.ts`
- Create: `dashboard_ui/tailwind.config.ts`
- Create: `dashboard_ui/postcss.config.js`
- Create: `dashboard_ui/index.html`

- [ ] **Step 1: Create `dashboard_ui/package.json`**

```json
{
  "name": "dashboard-ui",
  "version": "0.1.0",
  "private": true,
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc && vite build",
    "preview": "vite preview",
    "test": "vitest run",
    "test:ui": "vitest --ui"
  },
  "dependencies": {
    "react": "^18.3.1",
    "react-dom": "^18.3.1",
    "react-router-dom": "^6.28.0",
    "recharts": "^2.13.3",
    "lucide-react": "^0.454.0"
  },
  "devDependencies": {
    "@types/react": "^18.3.12",
    "@types/react-dom": "^18.3.1",
    "@vitejs/plugin-react": "^4.3.3",
    "typescript": "^5.6.3",
    "vite": "^5.4.10",
    "tailwindcss": "^3.4.15",
    "autoprefixer": "^10.4.20",
    "postcss": "^8.4.49",
    "@testing-library/react": "^16.0.1",
    "@testing-library/jest-dom": "^6.6.3",
    "vitest": "^2.1.5",
    "jsdom": "^25.0.1"
  }
}
```

- [ ] **Step 2: Create `dashboard_ui/tsconfig.json`**

```json
{
  "compilerOptions": {
    "target": "ES2020",
    "useDefineForClassFields": true,
    "lib": ["ES2020", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "skipLibCheck": true,
    "moduleResolution": "bundler",
    "allowImportingTsExtensions": true,
    "resolveJsonModule": true,
    "isolatedModules": true,
    "noEmit": true,
    "jsx": "react-jsx",
    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "noFallthroughCasesInSwitch": true
  },
  "include": ["src"],
  "references": [{ "path": "./tsconfig.node.json" }]
}
```

- [ ] **Step 3: Create `dashboard_ui/tsconfig.node.json`**

```json
{
  "compilerOptions": {
    "composite": true,
    "skipLibCheck": true,
    "module": "ESNext",
    "moduleResolution": "bundler",
    "allowSyntheticDefaultImports": true,
    "strict": true
  },
  "include": ["vite.config.ts"]
}
```

- [ ] **Step 4: Create `dashboard_ui/vite.config.ts`**

```typescript
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': 'http://localhost:8090',
    },
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: './src/test-setup.ts',
  },
})
```

- [ ] **Step 5: Create `dashboard_ui/tailwind.config.ts`**

```typescript
import type { Config } from 'tailwindcss'

export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        brand: {
          50: '#eff6ff',
          500: '#3b82f6',
          600: '#2563eb',
          900: '#1e3a8a',
        },
      },
    },
  },
  plugins: [],
} satisfies Config
```

- [ ] **Step 6: Create `dashboard_ui/postcss.config.js`**

```js
export default {
  plugins: {
    tailwindcss: {},
    autoprefixer: {},
  },
}
```

- [ ] **Step 7: Create `dashboard_ui/index.html`**

```html
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Kafka Pipeline — Observability</title>
  </head>
  <body class="bg-gray-950 text-gray-100">
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

- [ ] **Step 8: Install dependencies**

```bash
cd /home/ashwin/volleyball/volleyball/dashboard_ui
npm install
```

Expected: `node_modules/` created, no errors.

- [ ] **Step 9: Commit**

```bash
cd /home/ashwin/volleyball/volleyball
git add dashboard_ui/package.json dashboard_ui/tsconfig.json dashboard_ui/tsconfig.node.json dashboard_ui/vite.config.ts dashboard_ui/tailwind.config.ts dashboard_ui/postcss.config.js dashboard_ui/index.html dashboard_ui/package-lock.json
git commit -m "feat(dashboard): scaffold Vite + React + TypeScript + Tailwind project"
```

---

## Task 6: TypeScript Types + API Client

**Files:**
- Create: `dashboard_ui/src/api/types.ts`
- Create: `dashboard_ui/src/api/client.ts`

- [ ] **Step 1: Create `dashboard_ui/src/api/types.ts`**

```typescript
export interface ServiceSnapshot {
  raw: Record<string, number>;
  reachable: boolean;
}

export interface RawSnapshot {
  ts: number;
  sp: ServiceSnapshot;
  rw: ServiceSnapshot;
  dq: ServiceSnapshot;
  us: ServiceSnapshot;
  rs: ServiceSnapshot;
}

export interface DerivedMetrics {
  throughput_eps: number | null;
  success_rate_pct: number | null;
  avg_latency_ms: number | null;
  retry_backlog: number;
}

export interface CurrentMetrics {
  snapshot: RawSnapshot;
  derived: DerivedMetrics;
}

export interface HealthStatus {
  stream_processor: boolean;
  retry_worker: boolean;
  dlq_handler: boolean;
  user_service: boolean;
  result_service: boolean;
}

export interface ResultRow {
  event_id: string;
  user_id: number;
  action: string;
  user_name: string | null;
  tier: string | null;
  enriched_at: string;
  source: string;
}

export interface ResultStats {
  total: number;
  by_action: Record<string, number>;
  by_tier: Record<string, number>;
  by_source: Record<string, number>;
}

export interface ScenarioResult {
  ok?: boolean;
  message?: string;
  published?: number;
  replayed?: number;
  duration_sec?: number;
  user?: Record<string, unknown>;
}
```

- [ ] **Step 2: Create `dashboard_ui/src/api/client.ts`**

```typescript
import type {
  CurrentMetrics,
  HealthStatus,
  RawSnapshot,
  ResultRow,
  ResultStats,
  ScenarioResult,
} from './types'

const BASE = ''  // nginx proxies /api → dashboard-api

async function get<T>(path: string): Promise<T> {
  const r = await fetch(`${BASE}${path}`)
  if (!r.ok) throw new Error(`${r.status} ${r.statusText} — ${path}`)
  return r.json() as Promise<T>
}

async function post<T>(path: string, params?: Record<string, string | number>): Promise<T> {
  const url = new URL(`${location.origin}${BASE}${path}`)
  if (params) {
    Object.entries(params).forEach(([k, v]) => url.searchParams.set(k, String(v)))
  }
  const r = await fetch(url.toString(), { method: 'POST' })
  if (!r.ok) throw new Error(`${r.status} ${r.statusText} — ${path}`)
  return r.json() as Promise<T>
}

export const api = {
  currentMetrics: () => get<CurrentMetrics>('/api/metrics/current'),
  timeSeries: (window = 300) => get<RawSnapshot[]>(`/api/metrics/timeseries?window=${window}`),
  health: () => get<HealthStatus>('/api/health'),
  results: (limit = 50) => get<ResultRow[]>(`/api/results?limit=${limit}`),
  resultStats: () => get<ResultStats>('/api/results/stats'),
  simulateDown: () => post<ScenarioResult>('/api/scenarios/simulate-down'),
  restoreUser: () => post<ScenarioResult>('/api/scenarios/restore-user'),
  loadBurst: (rate = 200, duration = 10) =>
    post<ScenarioResult>('/api/scenarios/load-burst', { rate, duration }),
  replayDlq: (limit = 100) =>
    post<ScenarioResult>('/api/scenarios/replay-dlq', { limit }),
}
```

- [ ] **Step 3: Commit**

```bash
cd /home/ashwin/volleyball/volleyball
git add dashboard_ui/src/api/
git commit -m "feat(dashboard): add TypeScript types and API client"
```

---

## Task 7: Custom Hooks

**Files:**
- Create: `dashboard_ui/src/hooks/useCurrentMetrics.ts`
- Create: `dashboard_ui/src/hooks/useTimeSeries.ts`
- Create: `dashboard_ui/src/hooks/useHealth.ts`
- Create: `dashboard_ui/src/hooks/useResults.ts`
- Create: `dashboard_ui/src/test-setup.ts`

- [ ] **Step 1: Create `dashboard_ui/src/test-setup.ts`**

```typescript
import '@testing-library/jest-dom'
```

- [ ] **Step 2: Create `dashboard_ui/src/hooks/useCurrentMetrics.ts`**

```typescript
import { useEffect, useState } from 'react'
import { api } from '../api/client'
import type { CurrentMetrics } from '../api/types'

export function useCurrentMetrics(intervalMs = 2000) {
  const [data, setData] = useState<CurrentMetrics | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    const fetch = async () => {
      try {
        const result = await api.currentMetrics()
        if (!cancelled) { setData(result); setError(null) }
      } catch (e) {
        if (!cancelled) setError(String(e))
      }
    }
    fetch()
    const id = setInterval(fetch, intervalMs)
    return () => { cancelled = true; clearInterval(id) }
  }, [intervalMs])

  return { data, error }
}
```

- [ ] **Step 3: Create `dashboard_ui/src/hooks/useTimeSeries.ts`**

```typescript
import { useEffect, useState } from 'react'
import { api } from '../api/client'
import type { RawSnapshot } from '../api/types'

export function useTimeSeries(windowSec = 300, intervalMs = 3000) {
  const [data, setData] = useState<RawSnapshot[]>([])

  useEffect(() => {
    let cancelled = false
    const fetch = async () => {
      try {
        const result = await api.timeSeries(windowSec)
        if (!cancelled) setData(result)
      } catch { /* silent — chart just shows stale */ }
    }
    fetch()
    const id = setInterval(fetch, intervalMs)
    return () => { cancelled = true; clearInterval(id) }
  }, [windowSec, intervalMs])

  return data
}
```

- [ ] **Step 4: Create `dashboard_ui/src/hooks/useHealth.ts`**

```typescript
import { useEffect, useState } from 'react'
import { api } from '../api/client'
import type { HealthStatus } from '../api/types'

export function useHealth(intervalMs = 5000) {
  const [health, setHealth] = useState<HealthStatus | null>(null)

  useEffect(() => {
    let cancelled = false
    const fetch = async () => {
      try {
        const result = await api.health()
        if (!cancelled) setHealth(result)
      } catch { /* silent */ }
    }
    fetch()
    const id = setInterval(fetch, intervalMs)
    return () => { cancelled = true; clearInterval(id) }
  }, [intervalMs])

  return health
}
```

- [ ] **Step 5: Create `dashboard_ui/src/hooks/useResults.ts`**

```typescript
import { useEffect, useState } from 'react'
import { api } from '../api/client'
import type { ResultRow, ResultStats } from '../api/types'

export function useResults(intervalMs = 5000) {
  const [rows, setRows] = useState<ResultRow[]>([])
  const [stats, setStats] = useState<ResultStats | null>(null)

  useEffect(() => {
    let cancelled = false
    const fetch = async () => {
      try {
        const [r, s] = await Promise.all([api.results(50), api.resultStats()])
        if (!cancelled) { setRows(r); setStats(s) }
      } catch { /* silent */ }
    }
    fetch()
    const id = setInterval(fetch, intervalMs)
    return () => { cancelled = true; clearInterval(id) }
  }, [intervalMs])

  return { rows, stats }
}
```

- [ ] **Step 6: Commit**

```bash
cd /home/ashwin/volleyball/volleyball
git add dashboard_ui/src/hooks/ dashboard_ui/src/test-setup.ts
git commit -m "feat(dashboard): add React hooks for metrics, health, and results polling"
```

---

## Task 8: Shared UI Components

**Files:**
- Create: `dashboard_ui/src/components/KpiCard.tsx`
- Create: `dashboard_ui/src/components/ServiceHealthBar.tsx`
- Create: `dashboard_ui/src/components/NavBar.tsx`

- [ ] **Step 1: Create `dashboard_ui/src/components/KpiCard.tsx`**

```tsx
interface KpiCardProps {
  label: string
  value: string | number | null
  unit?: string
  highlight?: 'green' | 'amber' | 'red' | 'blue'
}

const highlights = {
  green: 'border-green-500 text-green-400',
  amber: 'border-amber-400 text-amber-300',
  red: 'border-red-500 text-red-400',
  blue: 'border-blue-500 text-blue-400',
}

export function KpiCard({ label, value, unit, highlight }: KpiCardProps) {
  const color = highlight ? highlights[highlight] : 'border-gray-700 text-gray-100'
  const display = value === null || value === undefined ? '—' : value

  return (
    <div className={`rounded-xl border bg-gray-900 p-4 flex flex-col gap-1 ${color}`}>
      <p className="text-xs font-medium uppercase tracking-widest text-gray-400">{label}</p>
      <p className="text-3xl font-bold">
        {display}
        {unit && <span className="ml-1 text-sm font-normal text-gray-400">{unit}</span>}
      </p>
    </div>
  )
}
```

- [ ] **Step 2: Create `dashboard_ui/src/components/ServiceHealthBar.tsx`**

```tsx
import type { HealthStatus } from '../api/types'

interface ServiceHealthBarProps {
  health: HealthStatus | null
}

const SERVICE_LABELS: [keyof HealthStatus, string][] = [
  ['stream_processor', 'Stream Processor'],
  ['retry_worker', 'Retry Worker'],
  ['dlq_handler', 'DLQ Handler'],
  ['user_service', 'User Service'],
  ['result_service', 'Result Service'],
]

export function ServiceHealthBar({ health }: ServiceHealthBarProps) {
  return (
    <div className="flex gap-3 flex-wrap">
      {SERVICE_LABELS.map(([key, label]) => {
        const up = health ? health[key] : null
        const color =
          up === null ? 'bg-gray-600' : up ? 'bg-green-500' : 'bg-red-500'
        return (
          <div key={key} className="flex items-center gap-1.5">
            <span className={`h-2.5 w-2.5 rounded-full ${color} animate-pulse`} />
            <span className="text-xs text-gray-300">{label}</span>
          </div>
        )
      })}
    </div>
  )
}
```

- [ ] **Step 3: Create `dashboard_ui/src/components/NavBar.tsx`**

```tsx
import { NavLink } from 'react-router-dom'
import type { HealthStatus } from '../api/types'
import { ServiceHealthBar } from './ServiceHealthBar'

interface NavBarProps {
  health: HealthStatus | null
}

const NAV_LINKS = [
  { to: '/', label: 'Overview' },
  { to: '/pipeline', label: 'Pipeline' },
  { to: '/fault-tolerance', label: 'Fault Tolerance' },
  { to: '/scenarios', label: 'Scenarios' },
  { to: '/results', label: 'Results' },
]

export function NavBar({ health }: NavBarProps) {
  return (
    <header className="sticky top-0 z-50 bg-gray-900 border-b border-gray-800 px-6 py-3">
      <div className="max-w-screen-xl mx-auto flex items-center justify-between gap-6">
        <div className="flex items-center gap-6">
          <h1 className="text-sm font-bold text-blue-400 uppercase tracking-widest whitespace-nowrap">
            Kafka Pipeline
          </h1>
          <nav className="flex gap-1">
            {NAV_LINKS.map(({ to, label }) => (
              <NavLink
                key={to}
                to={to}
                end={to === '/'}
                className={({ isActive }) =>
                  `px-3 py-1.5 rounded-lg text-sm font-medium transition-colors ${
                    isActive
                      ? 'bg-blue-600 text-white'
                      : 'text-gray-400 hover:text-white hover:bg-gray-800'
                  }`
                }
              >
                {label}
              </NavLink>
            ))}
          </nav>
        </div>
        <ServiceHealthBar health={health} />
      </div>
    </header>
  )
}
```

- [ ] **Step 4: Commit**

```bash
cd /home/ashwin/volleyball/volleyball
git add dashboard_ui/src/components/KpiCard.tsx dashboard_ui/src/components/ServiceHealthBar.tsx dashboard_ui/src/components/NavBar.tsx
git commit -m "feat(dashboard): add KpiCard, ServiceHealthBar, NavBar components"
```

---

## Task 9: Chart Components

**Files:**
- Create: `dashboard_ui/src/components/ThroughputChart.tsx`
- Create: `dashboard_ui/src/components/ConsumerLagChart.tsx`
- Create: `dashboard_ui/src/components/LatencyHistogram.tsx`
- Create: `dashboard_ui/src/components/EnrichmentFunnel.tsx`

- [ ] **Step 1: Create `dashboard_ui/src/components/ThroughputChart.tsx`**

```tsx
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, ReferenceLine,
} from 'recharts'
import type { RawSnapshot } from '../api/types'

interface ThroughputChartProps {
  snapshots: RawSnapshot[]
}

export function ThroughputChart({ snapshots }: ThroughputChartProps) {
  const data = snapshots.map((s, i) => {
    const prev = snapshots[i - 1]
    let eps: number | null = null
    if (prev && s.sp.reachable && prev.sp.reachable) {
      const dt = s.ts - prev.ts
      const enriched = s.sp.raw['stream_processor_enriched_events_total'] ?? 0
      const prevEnriched = prev.sp.raw['stream_processor_enriched_events_total'] ?? 0
      if (dt > 0) eps = Math.max(0, (enriched - prevEnriched) / dt)
    }
    return {
      time: new Date(s.ts * 1000).toLocaleTimeString(),
      eps,
    }
  })

  return (
    <div className="rounded-xl bg-gray-900 border border-gray-800 p-4">
      <h3 className="text-sm font-semibold text-gray-300 mb-3">Throughput (events/sec)</h3>
      <ResponsiveContainer width="100%" height={200}>
        <LineChart data={data} margin={{ top: 5, right: 10, left: 0, bottom: 5 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
          <XAxis dataKey="time" tick={{ fontSize: 11, fill: '#9ca3af' }} interval="preserveStartEnd" />
          <YAxis tick={{ fontSize: 11, fill: '#9ca3af' }} />
          <Tooltip
            contentStyle={{ backgroundColor: '#111827', border: '1px solid #374151', borderRadius: 8 }}
            labelStyle={{ color: '#f9fafb' }}
          />
          <Line
            type="monotone"
            dataKey="eps"
            stroke="#3b82f6"
            strokeWidth={2}
            dot={false}
            connectNulls={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}
```

- [ ] **Step 2: Create `dashboard_ui/src/components/ConsumerLagChart.tsx`**

```tsx
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from 'recharts'
import type { RawSnapshot } from '../api/types'

interface ConsumerLagChartProps {
  snapshot: RawSnapshot | null
}

function getLag(raw: Record<string, number>, topic: string, partition: number): number {
  const key = `stream_processor_consumer_lag_messages{partition="${partition}",topic="${topic}"}`
  const key2 = `stream_processor_consumer_lag_messages{topic="${topic}",partition="${partition}"}`
  return raw[key] ?? raw[key2] ?? 0
}

function getRetryLag(raw: Record<string, number>, partition: number): number {
  const key = `retry_worker_consumer_lag_messages{partition="${partition}",topic="retry-events"}`
  const key2 = `retry_worker_consumer_lag_messages{topic="retry-events",partition="${partition}"}`
  return raw[key] ?? raw[key2] ?? 0
}

export function ConsumerLagChart({ snapshot }: ConsumerLagChartProps) {
  const data = [0, 1, 2].map((p) => ({
    partition: `p${p}`,
    'raw-events': snapshot ? getLag(snapshot.sp.raw, 'raw-events', p) : 0,
    'retry-events': snapshot ? getRetryLag(snapshot.rw.raw, p) : 0,
  }))

  return (
    <div className="rounded-xl bg-gray-900 border border-gray-800 p-4">
      <h3 className="text-sm font-semibold text-gray-300 mb-3">Consumer Lag (messages)</h3>
      <ResponsiveContainer width="100%" height={200}>
        <BarChart data={data} margin={{ top: 5, right: 10, left: 0, bottom: 5 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
          <XAxis dataKey="partition" tick={{ fontSize: 12, fill: '#9ca3af' }} />
          <YAxis tick={{ fontSize: 11, fill: '#9ca3af' }} />
          <Tooltip
            contentStyle={{ backgroundColor: '#111827', border: '1px solid #374151', borderRadius: 8 }}
          />
          <Legend wrapperStyle={{ fontSize: 12 }} />
          <Bar dataKey="raw-events" fill="#3b82f6" radius={[4, 4, 0, 0]} />
          <Bar dataKey="retry-events" fill="#f59e0b" radius={[4, 4, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}
```

- [ ] **Step 3: Create `dashboard_ui/src/components/LatencyHistogram.tsx`**

```tsx
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts'
import type { RawSnapshot } from '../api/types'

interface LatencyHistogramProps {
  snapshot: RawSnapshot | null
}

const BUCKETS = [
  { le: '0.001', label: '1ms' },
  { le: '0.005', label: '5ms' },
  { le: '0.01', label: '10ms' },
  { le: '0.025', label: '25ms' },
  { le: '0.05', label: '50ms' },
  { le: '0.1', label: '100ms' },
  { le: '0.25', label: '250ms' },
  { le: '0.5', label: '500ms' },
  { le: '1.0', label: '1s' },
]

export function LatencyHistogram({ snapshot }: LatencyHistogramProps) {
  const raw = snapshot?.sp.raw ?? {}
  let prev = 0
  const data = BUCKETS.map(({ le, label }) => {
    const cum = raw[`stream_processor_processing_seconds_bucket{le="${le}"}`] ?? 0
    const count = Math.max(0, cum - prev)
    prev = cum
    return { label, count }
  })

  return (
    <div className="rounded-xl bg-gray-900 border border-gray-800 p-4">
      <h3 className="text-sm font-semibold text-gray-300 mb-3">Processing Latency Distribution</h3>
      <ResponsiveContainer width="100%" height={200}>
        <BarChart data={data} margin={{ top: 5, right: 10, left: 0, bottom: 5 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
          <XAxis dataKey="label" tick={{ fontSize: 10, fill: '#9ca3af' }} />
          <YAxis tick={{ fontSize: 11, fill: '#9ca3af' }} />
          <Tooltip
            contentStyle={{ backgroundColor: '#111827', border: '1px solid #374151', borderRadius: 8 }}
          />
          <Bar dataKey="count" fill="#8b5cf6" radius={[4, 4, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}
```

- [ ] **Step 4: Create `dashboard_ui/src/components/EnrichmentFunnel.tsx`**

```tsx
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Cell, ResponsiveContainer } from 'recharts'
import type { RawSnapshot } from '../api/types'

interface EnrichmentFunnelProps {
  snapshot: RawSnapshot | null
}

const COLORS = ['#3b82f6', '#10b981', '#f59e0b', '#ef4444']

export function EnrichmentFunnel({ snapshot }: EnrichmentFunnelProps) {
  const sp = snapshot?.sp.raw ?? {}
  const rw = snapshot?.rw.raw ?? {}
  const dq = snapshot?.dq.raw ?? {}

  const data = [
    { label: 'Consumed', value: sp['stream_processor_events_consumed_total'] ?? 0 },
    { label: 'Enriched', value: sp['stream_processor_enriched_events_total'] ?? 0 },
    { label: 'Retry Out', value: sp['stream_processor_retry_published_total'] ?? 0 },
    { label: 'DLQ', value: dq['dlq_handler_messages_total'] ?? 0 },
  ]

  return (
    <div className="rounded-xl bg-gray-900 border border-gray-800 p-4">
      <h3 className="text-sm font-semibold text-gray-300 mb-3">Pipeline Funnel (totals)</h3>
      <ResponsiveContainer width="100%" height={200}>
        <BarChart data={data} margin={{ top: 5, right: 10, left: 0, bottom: 5 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
          <XAxis dataKey="label" tick={{ fontSize: 12, fill: '#9ca3af' }} />
          <YAxis tick={{ fontSize: 11, fill: '#9ca3af' }} />
          <Tooltip
            contentStyle={{ backgroundColor: '#111827', border: '1px solid #374151', borderRadius: 8 }}
          />
          <Bar dataKey="value" radius={[4, 4, 0, 0]}>
            {data.map((_, idx) => (
              <Cell key={idx} fill={COLORS[idx]} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}
```

- [ ] **Step 5: Commit**

```bash
cd /home/ashwin/volleyball/volleyball
git add dashboard_ui/src/components/ThroughputChart.tsx dashboard_ui/src/components/ConsumerLagChart.tsx dashboard_ui/src/components/LatencyHistogram.tsx dashboard_ui/src/components/EnrichmentFunnel.tsx
git commit -m "feat(dashboard): add chart components (throughput, lag, latency, funnel)"
```

---

## Task 10: Scenario Panel + Activity Log Components

**Files:**
- Create: `dashboard_ui/src/components/ActivityLog.tsx`
- Create: `dashboard_ui/src/components/ScenarioPanel.tsx`

- [ ] **Step 1: Create `dashboard_ui/src/components/ActivityLog.tsx`**

```tsx
interface ActivityLogProps {
  entries: string[]
}

export function ActivityLog({ entries }: ActivityLogProps) {
  return (
    <div className="h-48 overflow-y-auto rounded-lg bg-gray-950 border border-gray-800 p-3 font-mono text-xs text-gray-400 space-y-1">
      {entries.length === 0 && (
        <p className="text-gray-600 italic">No activity yet. Trigger a scenario above.</p>
      )}
      {entries.map((e, i) => (
        <p key={i} className="leading-relaxed">{e}</p>
      ))}
    </div>
  )
}
```

- [ ] **Step 2: Create `dashboard_ui/src/components/ScenarioPanel.tsx`**

```tsx
import { useState } from 'react'
import { api } from '../api/client'
import { ActivityLog } from './ActivityLog'

export function ScenarioPanel() {
  const [log, setLog] = useState<string[]>([])
  const [running, setRunning] = useState<string | null>(null)

  const stamp = () => new Date().toLocaleTimeString()

  const run = async (name: string, fn: () => Promise<unknown>) => {
    setRunning(name)
    setLog((prev) => [`[${stamp()}] ▶ ${name} started...`, ...prev])
    try {
      const result = await fn()
      setLog((prev) => [
        `[${stamp()}] ✓ ${name} done — ${JSON.stringify(result)}`,
        ...prev,
      ])
    } catch (e) {
      setLog((prev) => [`[${stamp()}] ✗ ${name} failed — ${e}`, ...prev])
    } finally {
      setRunning(null)
    }
  }

  const btn = (label: string, key: string, onClick: () => Promise<unknown>, color = 'blue') => {
    const colors: Record<string, string> = {
      blue: 'bg-blue-600 hover:bg-blue-500',
      amber: 'bg-amber-600 hover:bg-amber-500',
      green: 'bg-green-600 hover:bg-green-500',
      red: 'bg-red-700 hover:bg-red-600',
    }
    return (
      <button
        key={key}
        onClick={() => run(label, onClick)}
        disabled={running !== null}
        className={`px-4 py-2 rounded-lg text-sm font-semibold text-white transition-colors disabled:opacity-50 disabled:cursor-not-allowed ${colors[color]}`}
      >
        {running === label ? '⏳ Running…' : label}
      </button>
    )
  }

  const scenarios = [
    {
      key: 'simulate',
      label: 'Simulate User Down',
      color: 'red',
      description: 'Deletes user 123 from the DB. Events for userId=123 will start flowing to retry → then DLQ.',
      fn: () => api.simulateDown(),
    },
    {
      key: 'restore',
      label: 'Restore User',
      color: 'green',
      description: 'Re-inserts user 123. Watch the retry queue drain and enriched events resume.',
      fn: () => api.restoreUser(),
    },
    {
      key: 'burst',
      label: 'Load Burst (10s)',
      color: 'blue',
      description: 'Fires 200 events/sec for 10 seconds. Watch the throughput chart spike and lag recover.',
      fn: () => api.loadBurst(200, 10),
    },
    {
      key: 'replay',
      label: 'Replay DLQ',
      color: 'amber',
      description: 'Re-injects the last 100 DLQ entries into raw-events for reprocessing.',
      fn: () => api.replayDlq(100),
    },
  ]

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {scenarios.map(({ key, label, color, description, fn }) => (
          <div key={key} className="rounded-xl bg-gray-900 border border-gray-800 p-5 space-y-3">
            <h3 className="font-semibold text-gray-100">{label}</h3>
            <p className="text-sm text-gray-400">{description}</p>
            {btn(label, key, fn, color)}
          </div>
        ))}
      </div>
      <div>
        <div className="flex items-center justify-between mb-2">
          <h3 className="text-sm font-semibold text-gray-300">Activity Log</h3>
          <button
            onClick={() => setLog([])}
            className="text-xs text-gray-500 hover:text-gray-300 transition-colors"
          >
            Clear
          </button>
        </div>
        <ActivityLog entries={log} />
      </div>
    </div>
  )
}
```

- [ ] **Step 3: Commit**

```bash
cd /home/ashwin/volleyball/volleyball
git add dashboard_ui/src/components/ActivityLog.tsx dashboard_ui/src/components/ScenarioPanel.tsx
git commit -m "feat(dashboard): add ScenarioPanel and ActivityLog components"
```

---

## Task 11: ResultsTable Component

**Files:**
- Create: `dashboard_ui/src/components/ResultsTable.tsx`

- [ ] **Step 1: Create `dashboard_ui/src/components/ResultsTable.tsx`**

```tsx
import { useState } from 'react'
import type { ResultRow } from '../api/types'

interface ResultsTableProps {
  rows: ResultRow[]
}

const ACTIONS = ['all', 'click', 'view', 'purchase', 'signup']
const TIERS = ['all', 'pro', 'standard']
const SOURCES = ['all', 'stream-processor', 'retry-worker']

export function ResultsTable({ rows }: ResultsTableProps) {
  const [actionFilter, setActionFilter] = useState('all')
  const [tierFilter, setTierFilter] = useState('all')
  const [sourceFilter, setSourceFilter] = useState('all')

  const filtered = rows.filter(
    (r) =>
      (actionFilter === 'all' || r.action === actionFilter) &&
      (tierFilter === 'all' || r.tier === tierFilter) &&
      (sourceFilter === 'all' || r.source === sourceFilter),
  )

  const select = (
    value: string,
    onChange: (v: string) => void,
    options: string[],
  ) => (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="rounded bg-gray-800 border border-gray-700 text-gray-300 text-xs px-2 py-1"
    >
      {options.map((o) => (
        <option key={o} value={o}>{o}</option>
      ))}
    </select>
  )

  return (
    <div className="space-y-3">
      <div className="flex gap-3 items-center flex-wrap">
        <span className="text-xs text-gray-400">Filter:</span>
        {select(actionFilter, setActionFilter, ACTIONS)}
        {select(tierFilter, setTierFilter, TIERS)}
        {select(sourceFilter, setSourceFilter, SOURCES)}
        <span className="text-xs text-gray-500">{filtered.length} rows</span>
      </div>
      <div className="overflow-x-auto rounded-xl border border-gray-800">
        <table className="w-full text-sm text-left">
          <thead className="bg-gray-900 text-gray-400 text-xs uppercase">
            <tr>
              {['Event ID', 'User ID', 'Action', 'Name', 'Tier', 'Enriched At', 'Source'].map((h) => (
                <th key={h} className="px-4 py-3 font-medium">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-800">
            {filtered.map((row) => (
              <tr key={row.event_id} className="bg-gray-950 hover:bg-gray-900 transition-colors">
                <td className="px-4 py-2 font-mono text-xs text-gray-500">
                  {row.event_id.slice(0, 8)}…
                </td>
                <td className="px-4 py-2 text-gray-300">{row.user_id}</td>
                <td className="px-4 py-2">
                  <span className="px-2 py-0.5 rounded-full text-xs font-medium bg-blue-900 text-blue-200">
                    {row.action}
                  </span>
                </td>
                <td className="px-4 py-2 text-gray-300">{row.user_name ?? '—'}</td>
                <td className="px-4 py-2">
                  {row.tier === 'pro' ? (
                    <span className="px-2 py-0.5 rounded-full text-xs font-medium bg-purple-900 text-purple-200">pro</span>
                  ) : (
                    <span className="text-gray-500 text-xs">{row.tier ?? '—'}</span>
                  )}
                </td>
                <td className="px-4 py-2 text-gray-500 text-xs">{row.enriched_at.slice(0, 19)}</td>
                <td className="px-4 py-2 text-xs text-gray-500">{row.source}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {filtered.length === 0 && (
          <p className="text-center text-gray-600 text-sm py-8">No results match the current filters.</p>
        )}
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Commit**

```bash
cd /home/ashwin/volleyball/volleyball
git add dashboard_ui/src/components/ResultsTable.tsx
git commit -m "feat(dashboard): add filterable ResultsTable component"
```

---

## Task 12: Pages

**Files:**
- Create: `dashboard_ui/src/pages/Overview.tsx`
- Create: `dashboard_ui/src/pages/Pipeline.tsx`
- Create: `dashboard_ui/src/pages/FaultTolerance.tsx`
- Create: `dashboard_ui/src/pages/Scenarios.tsx`
- Create: `dashboard_ui/src/pages/Results.tsx`

- [ ] **Step 1: Create `dashboard_ui/src/pages/Overview.tsx`**

```tsx
import { useCurrentMetrics } from '../hooks/useCurrentMetrics'
import { useTimeSeries } from '../hooks/useTimeSeries'
import { KpiCard } from '../components/KpiCard'
import { ThroughputChart } from '../components/ThroughputChart'
import { EnrichmentFunnel } from '../components/EnrichmentFunnel'

export function Overview() {
  const { data } = useCurrentMetrics()
  const series = useTimeSeries(300)

  const d = data?.derived
  const sp = data?.snapshot.sp.raw ?? {}

  const fmt = (v: number | null | undefined, decimals = 1) =>
    v == null ? null : Number(v.toFixed(decimals))

  return (
    <div className="space-y-6">
      <h2 className="text-lg font-semibold text-gray-100">Overview</h2>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <KpiCard
          label="Throughput"
          value={fmt(d?.throughput_eps)}
          unit="eps"
          highlight="blue"
        />
        <KpiCard
          label="Total Enriched"
          value={sp['stream_processor_enriched_events_total']?.toFixed(0) ?? null}
          highlight="green"
        />
        <KpiCard
          label="Success Rate"
          value={fmt(d?.success_rate_pct)}
          unit="%"
          highlight={
            d?.success_rate_pct == null ? undefined :
            d.success_rate_pct >= 95 ? 'green' :
            d.success_rate_pct >= 80 ? 'amber' : 'red'
          }
        />
        <KpiCard
          label="Avg Latency"
          value={fmt(d?.avg_latency_ms)}
          unit="ms"
          highlight="blue"
        />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <ThroughputChart snapshots={series} />
        <EnrichmentFunnel snapshot={data?.snapshot ?? null} />
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <KpiCard
          label="Duplicates Skipped"
          value={sp['stream_processor_duplicate_events_total']?.toFixed(0) ?? null}
        />
        <KpiCard
          label="Retry Backlog"
          value={fmt(d?.retry_backlog, 0)}
          highlight={
            d?.retry_backlog == null ? undefined :
            d.retry_backlog > 100 ? 'red' :
            d.retry_backlog > 10 ? 'amber' : 'green'
          }
        />
        <KpiCard
          label="User Updates Applied"
          value={sp['stream_processor_user_updates_applied_total']?.toFixed(0) ?? null}
        />
        <KpiCard
          label="DLQ Total"
          value={data?.snapshot.dq.raw['dlq_handler_messages_total']?.toFixed(0) ?? null}
          highlight="red"
        />
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Create `dashboard_ui/src/pages/Pipeline.tsx`**

```tsx
import { useCurrentMetrics } from '../hooks/useCurrentMetrics'
import { useTimeSeries } from '../hooks/useTimeSeries'
import { ConsumerLagChart } from '../components/ConsumerLagChart'
import { LatencyHistogram } from '../components/LatencyHistogram'
import { ThroughputChart } from '../components/ThroughputChart'

export function Pipeline() {
  const { data } = useCurrentMetrics()
  const series = useTimeSeries(300)

  return (
    <div className="space-y-6">
      <h2 className="text-lg font-semibold text-gray-100">Pipeline Deep Dive</h2>
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <ConsumerLagChart snapshot={data?.snapshot ?? null} />
        <LatencyHistogram snapshot={data?.snapshot ?? null} />
      </div>
      <ThroughputChart snapshots={series} />
    </div>
  )
}
```

- [ ] **Step 3: Create `dashboard_ui/src/pages/FaultTolerance.tsx`**

```tsx
import { useCurrentMetrics } from '../hooks/useCurrentMetrics'
import { KpiCard } from '../components/KpiCard'
import { PieChart, Pie, Cell, Tooltip, ResponsiveContainer, Legend } from 'recharts'

export function FaultTolerance() {
  const { data } = useCurrentMetrics()

  const rw = data?.snapshot.rw.raw ?? {}
  const sp = data?.snapshot.sp.raw ?? {}
  const dq = data?.snapshot.dq.raw ?? {}

  const retrySuccess = rw['retry_worker_success_total'] ?? 0
  const retryDlq = rw['retry_worker_dlq_published_total'] ?? 0
  const total = retrySuccess + retryDlq

  const pieData = [
    { name: 'Success', value: retrySuccess },
    { name: 'Sent to DLQ', value: retryDlq },
  ]

  const errors: [string, string][] = [
    ['sp.raw_event', 'stream_processor_errors_total{stage="raw_event"}'],
    ['sp.user_update', 'stream_processor_errors_total{stage="user_update"}'],
    ['rw.handle', 'retry_worker_errors_total{stage="handle"}'],
  ]

  return (
    <div className="space-y-6">
      <h2 className="text-lg font-semibold text-gray-100">Fault Tolerance</h2>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <KpiCard label="Retry Consumed" value={rw['retry_worker_messages_consumed_total']?.toFixed(0) ?? null} />
        <KpiCard label="Retry Success" value={retrySuccess.toFixed(0)} highlight="green" />
        <KpiCard label="Retry → DLQ" value={retryDlq.toFixed(0)} highlight="red" />
        <KpiCard label="DLQ Replayed" value={dq['dlq_handler_replay_total']?.toFixed(0) ?? null} highlight="amber" />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="rounded-xl bg-gray-900 border border-gray-800 p-4">
          <h3 className="text-sm font-semibold text-gray-300 mb-3">Retry Outcome (totals)</h3>
          {total > 0 ? (
            <ResponsiveContainer width="100%" height={200}>
              <PieChart>
                <Pie data={pieData} cx="50%" cy="50%" innerRadius={60} outerRadius={90} dataKey="value">
                  <Cell fill="#10b981" />
                  <Cell fill="#ef4444" />
                </Pie>
                <Tooltip contentStyle={{ backgroundColor: '#111827', border: '1px solid #374151', borderRadius: 8 }} />
                <Legend wrapperStyle={{ fontSize: 12 }} />
              </PieChart>
            </ResponsiveContainer>
          ) : (
            <p className="text-gray-600 text-sm text-center py-16">No retry data yet</p>
          )}
        </div>

        <div className="rounded-xl bg-gray-900 border border-gray-800 p-4">
          <h3 className="text-sm font-semibold text-gray-300 mb-3">Error Counters</h3>
          <table className="w-full text-sm">
            <thead>
              <tr className="text-gray-500 text-xs uppercase">
                <th className="text-left py-2">Stage</th>
                <th className="text-right py-2">Count</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-800">
              {errors.map(([label, key]) => {
                const svc = label.startsWith('sp') ? sp : rw
                const val = svc[key] ?? 0
                return (
                  <tr key={key}>
                    <td className="py-2 text-gray-400 font-mono text-xs">{label}</td>
                    <td className={`py-2 text-right font-bold ${val > 0 ? 'text-red-400' : 'text-gray-600'}`}>
                      {val.toFixed(0)}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
```

- [ ] **Step 4: Create `dashboard_ui/src/pages/Scenarios.tsx`**

```tsx
import { ScenarioPanel } from '../components/ScenarioPanel'

export function Scenarios() {
  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-lg font-semibold text-gray-100">Scenarios</h2>
        <p className="text-sm text-gray-400 mt-1">
          Trigger test cases and failure conditions, then watch the charts update in real time.
        </p>
      </div>
      <ScenarioPanel />
    </div>
  )
}
```

- [ ] **Step 5: Create `dashboard_ui/src/pages/Results.tsx`**

```tsx
import { useResults } from '../hooks/useResults'
import { ResultsTable } from '../components/ResultsTable'
import { KpiCard } from '../components/KpiCard'
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell } from 'recharts'

const COLORS = ['#3b82f6', '#10b981', '#f59e0b', '#8b5cf6', '#ec4899']

export function Results() {
  const { rows, stats } = useResults()

  const actionData = stats
    ? Object.entries(stats.by_action).map(([name, value]) => ({ name, value }))
    : []

  const tierData = stats
    ? Object.entries(stats.by_tier).map(([name, value]) => ({ name, value }))
    : []

  return (
    <div className="space-y-6">
      <h2 className="text-lg font-semibold text-gray-100">Result Store</h2>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <KpiCard label="Total Records" value={stats?.total ?? null} highlight="blue" />
        {actionData.slice(0, 3).map(({ name, value }) => (
          <KpiCard key={name} label={`Action: ${name}`} value={value} />
        ))}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="rounded-xl bg-gray-900 border border-gray-800 p-4">
          <h3 className="text-sm font-semibold text-gray-300 mb-3">By Action</h3>
          <ResponsiveContainer width="100%" height={160}>
            <BarChart data={actionData}>
              <XAxis dataKey="name" tick={{ fontSize: 12, fill: '#9ca3af' }} />
              <YAxis tick={{ fontSize: 11, fill: '#9ca3af' }} />
              <Tooltip contentStyle={{ backgroundColor: '#111827', border: '1px solid #374151', borderRadius: 8 }} />
              <Bar dataKey="value" radius={[4, 4, 0, 0]}>
                {actionData.map((_, i) => <Cell key={i} fill={COLORS[i % COLORS.length]} />)}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>

        <div className="rounded-xl bg-gray-900 border border-gray-800 p-4">
          <h3 className="text-sm font-semibold text-gray-300 mb-3">By Tier</h3>
          <ResponsiveContainer width="100%" height={160}>
            <BarChart data={tierData}>
              <XAxis dataKey="name" tick={{ fontSize: 12, fill: '#9ca3af' }} />
              <YAxis tick={{ fontSize: 11, fill: '#9ca3af' }} />
              <Tooltip contentStyle={{ backgroundColor: '#111827', border: '1px solid #374151', borderRadius: 8 }} />
              <Bar dataKey="value" radius={[4, 4, 0, 0]}>
                {tierData.map((_, i) => <Cell key={i} fill={COLORS[i % COLORS.length]} />)}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>

      <ResultsTable rows={rows} />
    </div>
  )
}
```

- [ ] **Step 6: Commit**

```bash
cd /home/ashwin/volleyball/volleyball
git add dashboard_ui/src/pages/
git commit -m "feat(dashboard): add all five dashboard pages"
```

---

## Task 13: App Entry Point + Routing

**Files:**
- Create: `dashboard_ui/src/main.tsx`
- Create: `dashboard_ui/src/App.tsx`
- Create: `dashboard_ui/src/index.css`

- [ ] **Step 1: Create `dashboard_ui/src/index.css`**

```css
@tailwind base;
@tailwind components;
@tailwind utilities;

body {
  font-family: 'Inter', ui-sans-serif, system-ui, sans-serif;
}
```

- [ ] **Step 2: Create `dashboard_ui/src/main.tsx`**

```tsx
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import './index.css'
import { App } from './App'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </StrictMode>,
)
```

- [ ] **Step 3: Create `dashboard_ui/src/App.tsx`**

```tsx
import { Route, Routes } from 'react-router-dom'
import { NavBar } from './components/NavBar'
import { useHealth } from './hooks/useHealth'
import { Overview } from './pages/Overview'
import { Pipeline } from './pages/Pipeline'
import { FaultTolerance } from './pages/FaultTolerance'
import { Scenarios } from './pages/Scenarios'
import { Results } from './pages/Results'

export function App() {
  const health = useHealth()

  return (
    <div className="min-h-screen bg-gray-950">
      <NavBar health={health} />
      <main className="max-w-screen-xl mx-auto px-6 py-6">
        <Routes>
          <Route path="/" element={<Overview />} />
          <Route path="/pipeline" element={<Pipeline />} />
          <Route path="/fault-tolerance" element={<FaultTolerance />} />
          <Route path="/scenarios" element={<Scenarios />} />
          <Route path="/results" element={<Results />} />
        </Routes>
      </main>
    </div>
  )
}
```

- [ ] **Step 4: Verify the build compiles without TypeScript errors**

```bash
cd /home/ashwin/volleyball/volleyball/dashboard_ui
npm run build 2>&1 | tail -20
```

Expected: `✓ built in` (no TypeScript errors)

- [ ] **Step 5: Commit**

```bash
cd /home/ashwin/volleyball/volleyball
git add dashboard_ui/src/main.tsx dashboard_ui/src/App.tsx dashboard_ui/src/index.css
git commit -m "feat(dashboard): wire up React Router and App entry point"
```

---

## Task 14: Nginx Config + UI Dockerfile

**Files:**
- Create: `dashboard_ui/nginx.conf`
- Create: `dashboard_ui/Dockerfile`

- [ ] **Step 1: Create `dashboard_ui/nginx.conf`**

```nginx
server {
    listen 80;
    root /usr/share/nginx/html;
    index index.html;

    location /api/ {
        proxy_pass http://dashboard-api:8090/api/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 60s;
    }

    location / {
        try_files $uri $uri/ /index.html;
    }
}
```

- [ ] **Step 2: Create `dashboard_ui/Dockerfile`**

```dockerfile
# Stage 1: build
FROM node:20-alpine AS build
WORKDIR /app
COPY dashboard_ui/package.json dashboard_ui/package-lock.json ./
RUN npm ci
COPY dashboard_ui/ ./
RUN npm run build

# Stage 2: serve
FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY dashboard_ui/nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
```

- [ ] **Step 3: Test the full Docker build locally**

```bash
cd /home/ashwin/volleyball/volleyball
docker build -f dashboard_ui/Dockerfile -t dashboard-ui-test . 2>&1 | tail -10
```

Expected: `Successfully built` (or `=> exporting to image`)

- [ ] **Step 4: Commit**

```bash
cd /home/ashwin/volleyball/volleyball
git add dashboard_ui/nginx.conf dashboard_ui/Dockerfile
git commit -m "feat(dashboard): add nginx config and multi-stage UI Dockerfile"
```

---

## Task 15: End-to-End Smoke Test

Verify the entire stack starts and the dashboard loads with live data.

- [ ] **Step 1: Build and start the full stack**

```bash
cd /home/ashwin/volleyball/volleyball
docker compose -f infra/docker-compose.yml up --build -d 2>&1 | tail -20
```

- [ ] **Step 2: Wait for schema registry health**

```bash
docker compose -f infra/docker-compose.yml ps
# Wait until schema-registry shows "healthy"
# Then check dashboard-api:
curl http://localhost:8090/api/health/self
```

Expected: `{"status":"ok"}`

- [ ] **Step 3: Wait ~10 seconds for first scrape, then verify metrics endpoint**

```bash
curl -s http://localhost:8090/api/metrics/current | python3 -m json.tool | head -30
```

Expected: JSON with `snapshot` and `derived` keys; `throughput_eps` may be null for the first few seconds.

- [ ] **Step 4: Open the dashboard in a browser**

Navigate to `http://localhost:8501`

Expected:
- NavBar visible with blue service health dots
- Overview page loads with KPI cards (values may be 0 initially, not `—`)
- Throughput chart starts filling in after ~10 s
- All tabs navigate without errors

- [ ] **Step 5: Trigger a scenario and verify it works**

In the browser → Scenarios tab → click **Load Burst (10s)** → observe:
- Activity log shows `▶ Load Burst (10s) started...` then `✓ ... published: N`
- Switch to Overview tab — throughput chart should show a spike

- [ ] **Step 6: Check logs for any errors**

```bash
docker compose -f infra/docker-compose.yml logs dashboard-api --tail=20
docker compose -f infra/docker-compose.yml logs dashboard-ui --tail=20
```

Expected: No error-level lines; `dashboard-api` shows scraper cycling, `dashboard-ui` shows nginx access logs.

- [ ] **Step 7: Final commit**

```bash
cd /home/ashwin/volleyball/volleyball
git add .
git commit -m "feat(dashboard): complete React + FastAPI dashboard — live charts, scenarios, results"
```

---

## Self-Review

### Spec Coverage Check

| Spec requirement | Task |
|---|---|
| Replace Streamlit | Task 4 (docker-compose removes old service) |
| `dashboard-api` scrapes all 5 services every 2s | Task 3 (`_scraper_loop`) |
| Rolling 10-min time series in memory | Task 1 (`MetricsStore(maxlen=300)`) |
| `/api/metrics/current` with derived metrics | Task 3 |
| `/api/metrics/timeseries` | Task 3 |
| `/api/health` | Task 3 |
| `/api/results` + `/api/results/stats` proxies | Task 3 |
| Scenario endpoints (simulate-down, restore, load-burst, replay-dlq) | Tasks 2 + 3 |
| Docker Compose: dashboard-api + dashboard-ui services | Task 4 |
| React + Vite + TypeScript + Tailwind scaffold | Task 5 |
| TypeScript types + API client | Task 6 |
| Polling hooks (2s/3s/5s) | Task 7 |
| NavBar with service health dots | Task 8 |
| KpiCard component | Task 8 |
| ThroughputChart (LineChart, time series) | Task 9 |
| ConsumerLagChart (BarChart, per partition) | Task 9 |
| LatencyHistogram | Task 9 |
| EnrichmentFunnel (BarChart) | Task 9 |
| ScenarioPanel + ActivityLog | Task 10 |
| ResultsTable with filters | Task 11 |
| Overview page | Task 12 |
| Pipeline page | Task 12 |
| FaultTolerance page (retry donut + error table) | Task 12 |
| Scenarios page | Task 12 |
| Results page with action/tier bar charts | Task 12 |
| App routing | Task 13 |
| Nginx config (proxy /api) | Task 14 |
| Multi-stage Dockerfile (node build + nginx serve) | Task 14 |
| End-to-end smoke test | Task 15 |

### No Placeholders

All code blocks in each task are complete implementations. No TBDs.

### Type Consistency

- `RawSnapshot` defined in `types.ts` Task 6, used in `ThroughputChart`, `ConsumerLagChart`, `LatencyHistogram`, `EnrichmentFunnel` — all reference `RawSnapshot` correctly.
- `ServiceSnapshot.raw` is `Record<string, number>` — Prometheus key lookups use string literal keys throughout.
- `api.simulateDown()`, `api.restoreUser()`, `api.loadBurst()`, `api.replayDlq()` defined in `client.ts` Task 6, called in `ScenarioPanel.tsx` Task 10.
- `useHealth()` returns `HealthStatus | null`, passed to `NavBar` and `ServiceHealthBar` — both typed correctly.
