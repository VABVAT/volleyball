"""Decode Kafka payloads for enriched-events (JSON or Confluent Avro)."""

from __future__ import annotations

import json
from typing import Any

from shared.avro_serde import confluent_decode
from shared.schemas import EnrichedEvent


def decode_enriched(
    data: bytes,
    use_avro: bool,
    parsed_schema: dict[str, Any] | None,
) -> EnrichedEvent:
    if use_avro and parsed_schema is not None:
        d = confluent_decode(parsed_schema, data)
        user = {
            "userId": d.get("userId"),
            "name": d.get("name"),
            "email": d.get("email"),
            "tier": d.get("tier"),
        }
        return EnrichedEvent(
            eventId=d["eventId"],
            userId=d["userId"],
            action=d["action"],
            user=user,
            enrichedAt=d.get("enrichedAt", ""),
            source=d.get("source", "unknown"),
        )
    return EnrichedEvent.model_validate_json(data.decode("utf-8"))
