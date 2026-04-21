"""Central topic names — single source of truth."""

RAW_EVENTS = "raw-events"
USER_UPDATES = "user-updates"
ENRICHED_EVENTS = "enriched-events"
RETRY_EVENTS = "retry-events"
DEAD_LETTER_QUEUE = "dead-letter-queue"

ALL_TOPICS = (
    RAW_EVENTS,
    USER_UPDATES,
    ENRICHED_EVENTS,
    RETRY_EVENTS,
    DEAD_LETTER_QUEUE,
)
