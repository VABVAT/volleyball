"""
Observability dashboard: metrics, consumer lag, DLQ replay, and result-store preview.
"""

from __future__ import annotations

import os
import re
import time
from datetime import timedelta

import httpx
import streamlit as st

STREAM = os.getenv("METRICS_STREAM_PROCESSOR", "http://localhost:8002")
RETRY = os.getenv("METRICS_RETRY_WORKER", "http://localhost:8004")
DLQ = os.getenv("METRICS_DLQ_HANDLER", "http://localhost:8005")
USER = os.getenv("METRICS_USER_SERVICE", "http://localhost:8080")
RESULT = os.getenv("RESULT_SERVICE_URL", "http://localhost:8006")


def fetch_metrics(url: str) -> str:
    try:
        r = httpx.get(f"{url.rstrip('/')}/metrics", timeout=3.0)
        r.raise_for_status()
        return r.text
    except Exception as e:
        return f"# error fetching {url}: {e}\n"


def parse_unlabeled_counter(text: str, name: str) -> float:
    pat = re.compile(rf"^{re.escape(name)}\s+([0-9.eE+-]+)\s*$", re.MULTILINE)
    m = pat.search(text)
    return float(m.group(1)) if m else 0.0


def parse_gauge(text: str, name: str, labels: dict[str, str]) -> float:
    """Match Prometheus line with given labels (label order may vary in exposition)."""
    prefix = name + "{"
    for line in text.splitlines():
        if not line.startswith(prefix):
            continue
        try:
            meta, value = line.rsplit(" ", 1)
        except ValueError:
            continue
        ok = all(f'{k}="{v}"' in meta for k, v in labels.items())
        if ok:
            try:
                return float(value)
            except ValueError:
                return 0.0
    return 0.0


def parse_histogram_sum(text: str, name: str) -> float | None:
    pat = re.compile(rf"^{re.escape(name)}_sum\s+([0-9.eE+-]+)\s*$", re.MULTILINE)
    m = pat.search(text)
    return float(m.group(1)) if m else None


def parse_histogram_count(text: str, name: str) -> float | None:
    pat = re.compile(rf"^{re.escape(name)}_count\s+([0-9.eE+-]+)\s*$", re.MULTILINE)
    m = pat.search(text)
    return float(m.group(1)) if m else None


st.set_page_config(page_title="Event Enrichment Pipeline", layout="wide")
st.title("Kafka Event Enrichment — Observability")

refresh = st.sidebar.slider("Refresh interval (seconds)", 1, 10, 3)

if "prev" not in st.session_state:
    st.session_state.prev = {}
if "prev_ts" not in st.session_state:
    st.session_state.prev_ts = None


@st.fragment(run_every=timedelta(seconds=refresh))
def render_metrics() -> None:
    sp = fetch_metrics(STREAM)
    rw = fetch_metrics(RETRY)
    dq = fetch_metrics(DLQ)
    us = fetch_metrics(USER)

    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.subheader("Stream processor")
        raw_total = parse_unlabeled_counter(sp, "stream_processor_events_consumed_total")
        now = time.time()
        eps = None
        if st.session_state.prev_ts is not None and "raw" in st.session_state.prev:
            dt = now - st.session_state.prev_ts
            if dt > 0:
                eps = (raw_total - st.session_state.prev["raw"]) / dt
        st.session_state.prev["raw"] = raw_total
        st.session_state.prev_ts = now
        st.metric("Approx events/sec (raw)", f"{eps:.1f}" if eps is not None else "n/a")
        st.metric("Raw consumed (total)", raw_total)
        st.metric("Enriched out", parse_unlabeled_counter(sp, "stream_processor_enriched_events_total"))
        st.metric("Duplicates skipped", parse_unlabeled_counter(sp, "stream_processor_duplicate_events_total"))
        st.metric("Retry topic out", parse_unlabeled_counter(sp, "stream_processor_retry_published_total"))
        st.metric("User updates applied", parse_unlabeled_counter(sp, "stream_processor_user_updates_applied_total"))
        sum_lat = parse_histogram_sum(sp, "stream_processor_processing_seconds")
        cnt_lat = parse_histogram_count(sp, "stream_processor_processing_seconds")
        avg = (sum_lat / cnt_lat) if (sum_lat is not None and cnt_lat and cnt_lat > 0) else None
        st.metric("Avg processing latency (s)", f"{avg:.4f}" if avg is not None else "n/a")
        st.caption("Consumer lag (messages, raw-events)")
        for p in range(3):
            lag = parse_gauge(
                sp,
                "stream_processor_consumer_lag_messages",
                {"topic": "raw-events", "partition": str(p)},
            )
            st.metric(f"Lag p{p}", f"{lag:.0f}")

    with col2:
        st.subheader("Retry worker")
        st.metric("Retry consumed", parse_unlabeled_counter(rw, "retry_worker_messages_consumed_total"))
        st.metric("Retry success", parse_unlabeled_counter(rw, "retry_worker_success_total"))
        st.metric("Re-published to retry", parse_unlabeled_counter(rw, "retry_worker_republished_total"))
        st.metric("DLQ published (from retry)", parse_unlabeled_counter(rw, "retry_worker_dlq_published_total"))
        for p in range(3):
            lag = parse_gauge(
                rw,
                "retry_worker_consumer_lag_messages",
                {"topic": "retry-events", "partition": str(p)},
            )
            st.metric(f"Retry lag p{p}", f"{lag:.0f}")

    with col3:
        st.subheader("DLQ handler")
        st.metric("DLQ records persisted", parse_unlabeled_counter(dq, "dlq_handler_messages_total"))
        st.metric("DLQ replayed", parse_unlabeled_counter(dq, "dlq_handler_replay_total"))
        if st.button("Replay DLQ → raw-events"):
            try:
                r = httpx.post(f"{DLQ.rstrip('/')}/replay", params={"limit": 100}, timeout=30.0)
                r.raise_for_status()
                st.success(f"Replayed {r.json().get('replayed', 0)} events")
            except Exception as e:
                st.error(str(e))

    with col4:
        st.subheader("User service")
        st.metric("Kafka publishes (user-updates)", parse_unlabeled_counter(us, "user_service_kafka_publish_total"))

    st.subheader("Result store (enriched-events → Postgres)")
    try:
        stats = httpx.get(f"{RESULT.rstrip('/')}/results/stats", timeout=3.0).json()
        st.metric("Total rows", stats.get("total", 0))
        st.json(stats)
        rows = httpx.get(f"{RESULT.rstrip('/')}/results?limit=10", timeout=3.0).json()
        st.dataframe(rows, use_container_width=True)
    except Exception as e:
        st.warning(f"Result service: {e}")

    with st.expander("Raw Prometheus — stream processor"):
        st.code(sp[:8000])


render_metrics()
