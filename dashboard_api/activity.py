"""In-memory activity log for the dashboard (recent control + scenario events)."""

from __future__ import annotations

import asyncio
import time
from collections import deque

_lock = asyncio.Lock()
_lines: deque[tuple[float, str]] = deque(maxlen=1200)


async def append_activity(message: str) -> None:
    async with _lock:
        _lines.append((time.time(), message))


async def get_activity(limit: int = 150) -> list[dict[str, str | float]]:
    async with _lock:
        tail = list(_lines)[-max(1, min(limit, 1200)) :]
    return [{"ts": t, "message": m} for t, m in tail]
