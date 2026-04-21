"""Pydantic models for Kafka payloads and HTTP APIs."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class RawEvent(BaseModel):
    """Inbound event from raw-events topic."""

    event_id: str = Field(..., alias="eventId")
    user_id: int = Field(..., alias="userId")
    action: str

    model_config = {"populate_by_name": True}


class UserProfile(BaseModel):
    """User snapshot replicated into the local state store."""

    user_id: int = Field(..., alias="userId")
    name: str
    email: str
    tier: str = "standard"

    model_config = {"populate_by_name": True}

    def to_enrichment_dict(self) -> dict[str, Any]:
        return {
            "userId": self.user_id,
            "name": self.name,
            "email": self.email,
            "tier": self.tier,
        }


class UserUpdateMessage(BaseModel):
    """Message on user-updates topic (full snapshot)."""

    user: UserProfile


class EnrichedEvent(BaseModel):
    """Output to enriched-events."""

    event_id: str = Field(..., alias="eventId")
    user_id: int = Field(..., alias="userId")
    action: str
    user: dict[str, Any] | None
    enriched_at: str = Field(default_factory=utc_now_iso)
    source: str = "stream-processor"

    model_config = {"populate_by_name": True}


class RetryEnvelope(BaseModel):
    """Payload for retry-events (re-process enrichment)."""

    event: RawEvent
    attempt: int = 1
    last_error: str | None = None
    trace_id: str | None = None
    # Unix timestamp (seconds); process only when time.time() >= retry_after (0 = immediate)
    retry_after: float = 0.0


class DeadLetterRecord(BaseModel):
    """Stored representation for DLQ topic and debug persistence."""

    event_id: str
    user_id: int
    action: str
    reason: str
    attempt: int
    payload: dict[str, Any]
    failed_at: str = Field(default_factory=utc_now_iso)
