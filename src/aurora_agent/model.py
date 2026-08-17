from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional

from ._version import __version__


class Phase(str, Enum):
    PROPOSED = "PROPOSED"
    AUTHORIZED = "AUTHORIZED"
    POLICY_PASSED = "POLICY_PASSED"
    PRECOMMITTED = "PRECOMMITTED"
    STARTED = "STARTED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


class OutcomeEvidenceStrength(str, Enum):
    O0 = "O0"
    O1 = "O1"
    O2 = "O2"
    O3 = "O3"


class Verdict(str, Enum):
    VALID = "VALID"
    INVALID = "INVALID"
    INCOMPLETE = "INCOMPLETE"
    UNSUPPORTED = "UNSUPPORTED"


@dataclass(frozen=True)
class Authorization:
    authorization_id: str
    action_id: str
    proposal_digest: str
    approved_by: str
    method: str
    created_at: str


@dataclass(frozen=True)
class PolicyPass:
    policy_pass_id: str
    action_id: str
    proposal_digest: str
    policy_id: str
    decision_reference: Optional[str]
    created_at: str


@dataclass(frozen=True)
class VerificationReport:
    verdict: Verdict
    action_id: Optional[str]
    proposal_digest: Optional[str]
    terminal_phase: Optional[str]
    outcome_strength: Optional[str]
    checks: tuple[dict[str, str], ...]
    errors: tuple[str, ...]
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_schema": "aurora.agent-sdk-verification-report.v0.1",
            "verifier": {"name": "aurora-agent", "version": __version__},
            "verdict": self.verdict.value,
            "action_id": self.action_id,
            "proposal_digest": self.proposal_digest,
            "terminal_phase": self.terminal_phase,
            "outcome_strength": self.outcome_strength,
            "checks": list(self.checks),
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "non_claims": [
                "VALID applies only to the supplied SDK evidence bundle.",
                "VALID does not prove capture completeness or absence of bypass paths.",
                "VALID does not prove external anchoring, certificate-path trust, qualified timestamp status, or legal admissibility.",
                "Outcome evidence strength is a declared classification, not physical-world truth.",
            ],
        }