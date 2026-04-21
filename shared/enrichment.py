"""Shared enrichment logic for stream processor and retry worker."""

from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
import structlog
from prometheus_client import Gauge
from redis.asyncio import Redis

from shared.redis_keys import user_profile_key
from shared.schemas import UserProfile

log = structlog.get_logger()

CIRCUIT_OPEN = Gauge(
    "user_service_circuit_breaker_open",
    "1 if HTTP circuit breaker to User Service is open",
)


@dataclass
class CircuitBreaker:
    failure_threshold: int = 5
    cooldown_sec: float = 30.0
    _failures: int = 0
    _opened_at: float = 0.0

    def is_open(self) -> bool:
        if self._opened_at and time.monotonic() - self._opened_at < self.cooldown_sec:
            CIRCUIT_OPEN.set(1.0)
            return True
        if self._opened_at:
            self._opened_at = 0.0
            self._failures = 0
        CIRCUIT_OPEN.set(0.0)
        return False

    def record_failure(self) -> None:
        self._failures += 1
        if self._failures >= self.failure_threshold:
            self._opened_at = time.monotonic()
            CIRCUIT_OPEN.set(1.0)

    def record_success(self) -> None:
        self._failures = 0
        self._opened_at = 0.0
        CIRCUIT_OPEN.set(0.0)


user_service_cb = CircuitBreaker()


async def redis_get_user(redis: Redis, user_id: int) -> dict[str, Any] | None:
    raw = await redis.get(user_profile_key(user_id))
    if not raw:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    return json.loads(raw)


async def redis_set_user(redis: Redis, profile: UserProfile) -> None:
    await redis.set(user_profile_key(profile.user_id), json.dumps(profile.to_enrichment_dict()))


async def fetch_user_http(
    client: httpx.AsyncClient,
    base_url: str,
    user_id: int,
    max_retries: int = 3,
) -> UserProfile | None:
    """
    Rare fallback: call User Service with bounded retries and exponential backoff.
    Uses a circuit breaker to avoid hammering a failed upstream.
    """
    if user_service_cb.is_open():
        log.warning("circuit_breaker_open", user_id=user_id)
        return None

    url = f"{base_url.rstrip('/')}/user/{user_id}"
    last_exc: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            r = await client.get(url, timeout=5.0)
            if r.status_code == 404:
                log.warning("user_not_found_http", user_id=user_id)
                user_service_cb.record_success()
                return None
            r.raise_for_status()
            data = r.json()
            prof = UserProfile.model_validate(data)
            user_service_cb.record_success()
            return prof
        except Exception as e:
            last_exc = e
            if attempt >= max_retries:
                break
            delay = (2 ** (attempt - 1)) + random.random() * 0.2
            log.warning(
                "user_http_retry",
                user_id=user_id,
                attempt=attempt,
                error=str(e),
                sleep_s=round(delay, 3),
            )
            import asyncio

            await asyncio.sleep(delay)
    log.error("user_http_failed", user_id=user_id, error=str(last_exc))
    user_service_cb.record_failure()
    return None


async def resolve_user(
    redis: Redis,
    http: httpx.AsyncClient,
    user_service_url: str,
    user_id: int,
    local_cache: dict[int, tuple[dict[str, Any], float]] | None,
    cache_ttl_sec: float,
    http_retries: int,
) -> tuple[dict[str, Any] | None, str | None]:
    """
    Returns (user_dict, error_reason). user_dict None means unresolved.
    """
    now = time.monotonic()
    if local_cache is not None and user_id in local_cache:
        data, exp = local_cache[user_id]
        if exp > now:
            return data, None
        del local_cache[user_id]

    u = await redis_get_user(redis, user_id)
    if u is not None:
        if local_cache is not None:
            local_cache[user_id] = (u, now + cache_ttl_sec)
        return u, None

    prof = await fetch_user_http(http, user_service_url, user_id, max_retries=http_retries)
    if prof is not None:
        await redis_set_user(redis, prof)
        d = prof.to_enrichment_dict()
        if local_cache is not None:
            local_cache[user_id] = (d, now + cache_ttl_sec)
        return d, None

    return None, "user_unresolved_after_http"
