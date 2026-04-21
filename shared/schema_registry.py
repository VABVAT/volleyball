"""Confluent Schema Registry helpers (register subject, cache schema id)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import httpx

REGISTRY_URL = os.getenv("SCHEMA_REGISTRY_URL", "http://localhost:8081").rstrip("/")

_cache: dict[str, int] = {}


def _subject_value(subject_base: str) -> str:
    return f"{subject_base}-value"


async def ensure_schema_id(subject_base: str, schema_json: dict[str, Any]) -> int:
    key = _subject_value(subject_base)
    if key in _cache:
        return _cache[key]
    payload = {"schema": json.dumps(schema_json)}
    subj = _subject_value(subject_base)
    url = f"{REGISTRY_URL}/subjects/{subj}/versions"
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(url, json=payload)
        if r.status_code in (200, 201):
            sid = int(r.json()["id"])
        elif r.status_code == 409:
            meta = await client.get(f"{REGISTRY_URL}/subjects/{subj}/versions/latest")
            meta.raise_for_status()
            sid = int(meta.json()["id"])
        else:
            r.raise_for_status()
    _cache[key] = sid
    return sid


def load_avro_json(relative: str) -> dict[str, Any]:
    path = Path(__file__).resolve().parent / "avro" / relative
    return json.loads(path.read_text(encoding="utf-8"))
