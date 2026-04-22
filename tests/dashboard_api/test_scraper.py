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

