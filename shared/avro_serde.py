"""Confluent wire format + fastavro for record payloads."""

from __future__ import annotations

import io
import struct
from typing import Any

import fastavro

MAGIC = 0


def parse_schema(schema: dict[str, Any]) -> dict[str, Any]:
    return fastavro.parse_schema(schema)


def confluent_encode(schema_id: int, parsed_schema: dict[str, Any], record: dict[str, Any]) -> bytes:
    buf = io.BytesIO()
    buf.write(struct.pack(">bI", MAGIC, schema_id))
    fastavro.schemaless_writer(buf, parsed_schema, record)
    return buf.getvalue()


def confluent_decode(parsed_schema: dict[str, Any], data: bytes) -> dict[str, Any]:
    if len(data) < 5:
        raise ValueError("payload too short")
    buf = io.BytesIO(data[5:])
    return fastavro.schemaless_reader(buf, parsed_schema)
