"""Frozen client identities for AURORA evidence ingestion v0.1."""

RUN_SCHEMA_ID = "aurora-evidence-ingestion-run"
RUN_SCHEMA_VERSION = "0.1"
EVENT_SCHEMA_ID = "aurora-evidence-ingestion-event"
EVENT_SCHEMA_VERSION = "0.1"
FINALIZE_SCHEMA_ID = "aurora-evidence-ingestion-finalize"
FINALIZE_SCHEMA_VERSION = "0.1"

CAPTURE_DIGEST_ONLY = "DIGEST_ONLY"
CAPTURE_REDACTED = "REDACTED"
CAPTURE_FULL_PAYLOAD = "FULL_PAYLOAD"
CAPTURE_MODES = (
    CAPTURE_DIGEST_ONLY,
    CAPTURE_REDACTED,
    CAPTURE_FULL_PAYLOAD,
)

EVENT_TYPES = (
    "prompt",
    "retrieved_context",
    "model_invocation",
    "model_response",
    "tool_request",
    "authorization",
    "tool_execution",
    "tool_outcome",
    "human_review",
    "final_decision",
    "runtime_failure",
)

STATE_PENDING = "PENDING"
STATE_SUBMITTING = "SUBMITTING"
STATE_ACKNOWLEDGED = "ACKNOWLEDGED"
STATE_CONFLICT = "CONFLICT"
STATE_REJECTED = "REJECTED"
TERMINAL_STATES = {STATE_ACKNOWLEDGED, STATE_CONFLICT, STATE_REJECTED}

LOCAL_RUN_OPEN = "OPEN"
LOCAL_RUN_FINALIZE_QUEUED = "FINALIZE_QUEUED"

RETRYABLE_STATUS = {500, 502, 503, 504}
