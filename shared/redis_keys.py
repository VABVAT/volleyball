"""Redis key helpers for idempotency and user projection."""


def user_profile_key(user_id: int) -> str:
    return f"user:profile:{user_id}"


def idempotency_key(event_id: str) -> str:
    return f"idem:{event_id}"
