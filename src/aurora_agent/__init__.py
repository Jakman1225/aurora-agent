"""AURORA Agent Evidence SDK with the Stage F Data Lifecycle surface."""

from ._version import __version__

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
from .human_approval import (
    HUMAN_APPROVAL_DECLARED_STATES,
    HUMAN_APPROVAL_PROFILE_ID,
    HUMAN_APPROVAL_PROFILE_VERSION,
    HUMAN_APPROVAL_REVIEW_LEVELS,
    HumanApprovalAPIError,
    HumanApprovalClient,
    HumanApprovalTransportError,
    build_approval_requirement,
    build_policy_requirement_binding,
)
from .amendments import (
    AMENDMENT_PROFILE_ID,
    AMENDMENT_PROFILE_VERSION,
    AMENDMENT_TYPES,
    LIFECYCLE_EFFECTS,
    LIFECYCLE_ROLES,
    AmendmentAPIError,
    AmendmentClient,
    AmendmentTransportError,
    build_amendment_request,
    build_amendment_request_from_lifecycle,
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
from .data_lifecycle import (
    DATA_LIFECYCLE_PROFILE_ID,
    DATA_LIFECYCLE_PROFILE_VERSION,
    DATA_LIFECYCLE_RECORD_TYPES,
    DataLifecycleAPIError,
    DataLifecycleClient,
    DataLifecycleTransportError,
)

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
    "HUMAN_APPROVAL_DECLARED_STATES", "HUMAN_APPROVAL_PROFILE_ID",
    "HUMAN_APPROVAL_PROFILE_VERSION", "HUMAN_APPROVAL_REVIEW_LEVELS",
    "HumanApprovalAPIError", "HumanApprovalClient", "HumanApprovalTransportError",
    "build_approval_requirement", "build_policy_requirement_binding",
    "AMENDMENT_PROFILE_ID", "AMENDMENT_PROFILE_VERSION", "AMENDMENT_TYPES",
    "LIFECYCLE_EFFECTS", "LIFECYCLE_ROLES", "AmendmentAPIError",
    "AmendmentClient", "AmendmentTransportError", "build_amendment_request",
    "build_amendment_request_from_lifecycle",
    "AI_DECISION_CAPTURE_MODES", "AI_DECISION_EVIDENCE_COMPLETENESS",
    "AI_DECISION_PROFILE_ID", "AI_DECISION_PROFILE_VERSION",
    "AI_DECISION_SCHEMA_ID", "AI_DECISION_SCHEMA_VERSION",
    "AI_DECISION_SCORE_DIRECTIONS", "AI_DECISION_SCORE_SCALE_KINDS",
    "AI_DECISION_SCORE_TRANSFORMS", "AIDecisionAPIError",
    "AIDecisionClient", "AIDecisionTransportError",
    "build_ai_decision_request", "build_evidence_assessment",
    "build_evidence_flag", "build_policy_context",
    "build_score_interpretation", "canonical_decimal",
    "DATA_LIFECYCLE_PROFILE_ID", "DATA_LIFECYCLE_PROFILE_VERSION",
    "DATA_LIFECYCLE_RECORD_TYPES", "DataLifecycleAPIError",
    "DataLifecycleClient", "DataLifecycleTransportError",
    "__version__",
]