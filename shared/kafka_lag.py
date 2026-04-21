"""Consumer lag estimation: log-end minus committed group offset per partition."""

from __future__ import annotations

import asyncio
import logging

from aiokafka import AIOKafkaConsumer
from aiokafka.admin import AIOKafkaAdminClient
from aiokafka.structs import TopicPartition
from prometheus_client import Gauge

log = logging.getLogger(__name__)


async def refresh_lag(
    bootstrap: str,
    topic: str,
    group_id: str,
    gauge: Gauge,
) -> None:
    admin = AIOKafkaAdminClient(bootstrap_servers=bootstrap)
    await admin.start()
    c = AIOKafkaConsumer(bootstrap_servers=bootstrap)
    await c.start()
    try:
        parts = c.partitions_for_topic(topic)
        if not parts:
            return
        tps = [TopicPartition(topic, p) for p in sorted(parts)]
        end_map = await c.end_offsets(tps)
        committed_map = await admin.list_consumer_group_offsets(group_id, partitions=tps)
        for tp in tps:
            end_off = end_map.get(tp)
            if end_off is None:
                continue
            meta = committed_map.get(tp)
            co = meta.offset if meta is not None else 0
            lag = max(0, int(end_off) - int(co))
            gauge.labels(topic, str(tp.partition)).set(lag)
    except Exception as e:
        log.warning("lag_refresh_failed", extra={"topic": topic, "group": group_id, "error": str(e)})
    finally:
        await c.stop()
        await admin.close()


async def lag_loop(
    bootstrap: str,
    shutdown: asyncio.Event,
    gauges: list[tuple[str, str, Gauge]],
    interval_sec: float = 10.0,
) -> None:
    while not shutdown.is_set():
        for topic, group_id, gauge in gauges:
            await refresh_lag(bootstrap, topic, group_id, gauge)
            await asyncio.sleep(0)
        try:
            await asyncio.wait_for(shutdown.wait(), timeout=interval_sec)
        except asyncio.TimeoutError:
            pass
