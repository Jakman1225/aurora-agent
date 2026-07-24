"""AURORA Agent Evidence SDK v0.3 with local and remote ingestion surfaces."""

from .boundary import Boundary, FieldRule
from .canonical import (
    CANONICALIZATION_PROFILE,
    CANONICALIZATION_VERSION,
    HASH_ALGORITHM,
    canonical_bytes,
    commitment,
)
from .exceptions import (
    AuroraAgentError,
    BoundaryViolation,
    CanonicalizationError,
    LifecycleError,
    VerificationError,
)
from .model import Authorization, OutcomeEvidenceStrength, Phase, PolicyPass, VerificationReport, Verdict
from .sdk import Action, Aurora, Execution
from .verifier import format_report, verify_bundle
from .ingestion import IngestionClient, RunSession
from .ingestion_outbox import IngestionOutbox, OutboxConflict, OutboxItem
from .adapters import ClaudeAgentCaptureAdapter, JAKROWD3IngestionObserver
from .quickstart import QuickstartError, QuickstartResult, QuickstartRunner

__version__ = "0.3.1"

__all__ = [
    "Action", "Aurora", "Authorization", "AuroraAgentError", "Boundary",
    "BoundaryViolation", "CANONICALIZATION_PROFILE", "CANONICALIZATION_VERSION",
    "CanonicalizationError", "Execution", "FieldRule", "HASH_ALGORITHM",
    "LifecycleError", "OutcomeEvidenceStrength", "Phase", "PolicyPass",
    "VerificationError", "VerificationReport", "Verdict", "canonical_bytes",
    "commitment", "format_report", "verify_bundle", "IngestionClient",
    "RunSession", "IngestionOutbox", "OutboxConflict", "OutboxItem",
    "ClaudeAgentCaptureAdapter",
    "JAKROWD3IngestionObserver",
    "QuickstartError", "QuickstartResult", "QuickstartRunner",
]
