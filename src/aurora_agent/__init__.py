"""AURORA Agent Evidence SDK v0.6 with action, ingestion, AI Output, and AI Decision surfaces."""

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
from .ai_decisions import (
    AI_DECISION_CAPTURE_MODES,
    AI_DECISION_EVIDENCE_COMPLETENESS,
    AI_DECISION_PROFILE_ID,
    AI_DECISION_PROFILE_VERSION,
    AI_DECISION_SCHEMA_ID,
    AI_DECISION_SCHEMA_VERSION,
    AI_DECISION_SCORE_DIRECTIONS,
    AI_DECISION_SCORE_SCALE_KINDS,
    AI_DECISION_SCORE_TRANSFORMS,
    AIDecisionAPIError,
    AIDecisionClient,
    AIDecisionTransportError,
    build_ai_decision_request,
    build_evidence_assessment,
    build_evidence_flag,
    build_policy_context,
    build_score_interpretation,
    canonical_decimal,
)
from .ai_outputs import (
    AI_OUTPUT_CAPTURE_MODES,
    AI_OUTPUT_FORMATS,
    AI_OUTPUT_PROFILE_ID,
    AI_OUTPUT_PROFILE_VERSION,
    AI_OUTPUT_SCHEMA_ID,
    AI_OUTPUT_SCHEMA_VERSION,
    AIOutputAPIError,
    AIOutputClient,
    AIOutputTransportError,
    content_digest,
)

__version__ = "0.6.0"

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
    "AI_OUTPUT_CAPTURE_MODES", "AI_OUTPUT_FORMATS", "AI_OUTPUT_PROFILE_ID",
    "AI_OUTPUT_PROFILE_VERSION", "AI_OUTPUT_SCHEMA_ID", "AI_OUTPUT_SCHEMA_VERSION",
    "AIOutputAPIError", "AIOutputClient", "AIOutputTransportError",
    "content_digest",
    "AI_DECISION_CAPTURE_MODES", "AI_DECISION_EVIDENCE_COMPLETENESS",
    "AI_DECISION_PROFILE_ID", "AI_DECISION_PROFILE_VERSION",
    "AI_DECISION_SCHEMA_ID", "AI_DECISION_SCHEMA_VERSION",
    "AI_DECISION_SCORE_DIRECTIONS", "AI_DECISION_SCORE_SCALE_KINDS",
    "AI_DECISION_SCORE_TRANSFORMS", "AIDecisionAPIError",
    "AIDecisionClient", "AIDecisionTransportError",
    "build_ai_decision_request", "build_evidence_assessment",
    "build_evidence_flag", "build_policy_context",
    "build_score_interpretation", "canonical_decimal",
]