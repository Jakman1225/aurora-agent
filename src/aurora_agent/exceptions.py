from __future__ import annotations


class AuroraAgentError(Exception):
    """Base SDK error."""


class CanonicalizationError(AuroraAgentError, TypeError):
    """Input cannot be committed under the frozen profile."""


class BoundaryViolation(AuroraAgentError, ValueError):
    """Invocation does not satisfy the declared evidence boundary."""


class LifecycleError(AuroraAgentError, RuntimeError):
    """Requested lifecycle transition is invalid or replayed."""


class VerificationError(AuroraAgentError):
    """Bundle verification could not be completed normally."""
