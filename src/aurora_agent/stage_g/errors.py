"""Recovery instructions never imply permission to repeat an external write."""

class StageGError(Exception):
    def __init__(self, code, *, status=None, recovery="NO_RETRY"):
        self.code, self.status, self.recovery = code, status, recovery
        super().__init__(code)

class AuthorizationDenied(StageGError):
    pass

class UnsupportedProfile(StageGError):
    pass

class MissingEvidence(StageGError):
    pass

class SealPending(StageGError):
    def __init__(self, code="SEAL_RECEIPT_PENDING", **kwargs):
        super().__init__(code, recovery="RECOVER_SEAL_RECEIPT", **kwargs)

class UncertainExecution(StageGError):
    def __init__(self, code="EXECUTION_UNCERTAIN", **kwargs):
        super().__init__(code, recovery="QUERY_ONLY", **kwargs)

class ProtocolError(StageGError):
    pass

def api_error(code, status):
    if code == "SEAL_RECEIPT_PENDING":
        return SealPending(status=status)
    if status in (401, 403) or code in {"CONTROL_DENIED", "AUTHORIZATION_REVOKED"}:
        return AuthorizationDenied(code, status=status)
    if "UNSUPPORTED" in code or "NOT_SUPPORTED" in code:
        return UnsupportedProfile(code, status=status)
    if "MISSING" in code or "NOT_FOUND" in code:
        return MissingEvidence(code, status=status)
    return StageGError(code, status=status)
