"""Prometheus metrics scraper and rolling time-series store."""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from math import isfinite


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
            val = float(val_str.strip())
            if not isfinite(val):
                continue
            result[key.strip()] = val
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

