#!/usr/bin/env python3
"""Quick sanity check for shared schemas (run with PYTHONPATH=.)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shared.schemas import EnrichedEvent, RawEvent, RetryEnvelope, UserProfile


def main() -> None:
    raw = RawEvent.model_validate({"eventId": "e1", "userId": 1, "action": "click"})
    assert raw.event_id == "e1"
    u = UserProfile(userId=1, name="A", email="a@b.com", tier="pro")
    en = EnrichedEvent(eventId="e1", userId=1, action="click", user=u.to_enrichment_dict())
    assert "userId" in json.loads(en.model_dump_json(by_alias=True))
    rj = RetryEnvelope(event=raw, attempt=1, last_error="x").model_dump_json(by_alias=True)
    RetryEnvelope.model_validate_json(rj)
    print("ok: schema round-trip")


if __name__ == "__main__":
    main()
