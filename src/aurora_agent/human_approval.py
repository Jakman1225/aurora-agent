from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .canonical import canonical_bytes
from .exceptions import AuroraAgentError

HUMAN_APPROVAL_PROFILE_ID = "auroraseal.human_review"
HUMAN_APPROVAL_PROFILE_VERSION = "1.0"
HUMAN_APPROVAL_REVIEW_LEVELS = (
    "level_1",
    "level_2",
    "level_3",
    "executive",
    "external",
)
HUMAN_APPROVAL_DECLARED_STATES = (
    "human_reviewed",
    "human_approved",
    "human_rejected",
    "human_overridden",
    "second_reviewer_required",
    "multi_party_approved",
    "escalated",
    "deferred",
)

_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_ROLE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,99}$")


class HumanApprovalTransportError(AuroraAgentError):
    """Network or protocol failure before a valid AURORA response is available."""


class HumanApprovalAPIError(AuroraAgentError):
    def __init__(
        self,
        *,
        status: int,
        code: str,
        detail: str,
        retry_permitted: bool = False,
    ) -> None:
        super().__init__(f"HTTP {status} {code}: {detail}")
        self.status = int(status)
        self.code = str(code)
        self.detail = str(detail)
        self.retry_permitted = bool(retry_permitted)


@dataclass(frozen=True)
class HumanApprovalHttpResponse:
    status: int
    body: bytes
    headers: Mapping[str, str]


class HumanApprovalHttpTransport:
    """In-memory API-key + optional human-reviewer token transport.

    reviewer_token is sent only as X-Reviewer-Authorization for calls that
    require a human identity binding. It is never persisted by this SDK.
    """

    def __init__(self, *, base_url: str, api_key: str, timeout: float = 20.0) -> None:
        if not isinstance(base_url, str) or not base_url.startswith(("https://", "http://")):
            raise ValueError("base_url must be an HTTP(S) URL")
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError("api_key must not be empty")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = float(timeout)

    def request(
        self,
        *,
        method: str,
        endpoint: str,
        body: bytes = b"",
        idempotency_key: Optional[str] = None,
        reviewer_token: Optional[str] = None,
        accept: str = "application/json",
    ) -> HumanApprovalHttpResponse:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": accept,
        }
        if body:
            headers["Content-Type"] = "application/json"
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        if reviewer_token:
            headers["X-Reviewer-Authorization"] = f"Bearer {reviewer_token}"
        request = Request(
            self.base_url + endpoint,
            data=body if method not in {"GET", "HEAD"} and body else None,
            method=method,
            headers=headers,
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return HumanApprovalHttpResponse(
                    status=int(response.status),
                    body=response.read(),
                    headers=dict(response.headers.items()),
                )
        except HTTPError as exc:
            return HumanApprovalHttpResponse(
                status=int(exc.code),
                body=exc.read(),
                headers=dict(exc.headers.items()),
            )
        except URLError as exc:
            raise HumanApprovalTransportError(str(exc.reason)) from exc
        except OSError as exc:
            raise HumanApprovalTransportError(str(exc)) from exc


def _idempotency_key(operation: str) -> str:
    return f"auroraseal.human-approval.{operation}:{uuid.uuid4().hex}"


def _json_value(raw: bytes) -> Any:
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HumanApprovalTransportError("AURORA returned invalid JSON") from exc


def _raise_api_error(response: HumanApprovalHttpResponse) -> None:
    try:
        payload = _json_value(response.body)
    except HumanApprovalTransportError:
        text = response.body.decode("utf-8", errors="replace")[:500]
        raise HumanApprovalAPIError(
            status=response.status,
            code="HTTP_ERROR",
            detail=text or "AURORA request failed",
        )
    if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
        error = payload["error"]
        code = str(error.get("code") or "HTTP_ERROR")
        detail = str(error.get("detail") or "AURORA request failed")
        retry = bool(error.get("retry_permitted"))
    elif isinstance(payload, dict):
        code = "HTTP_ERROR"
        detail = str(payload.get("detail") or payload.get("message") or "AURORA request failed")
        retry = False
    else:
        code = "HTTP_ERROR"
        detail = "AURORA request failed"
        retry = False
    raise HumanApprovalAPIError(
        status=response.status,
        code=code,
        detail=detail,
        retry_permitted=retry,
    )


def _identifier(name: str, value: str) -> str:
    text = str(value or "")
    if _IDENTIFIER_RE.fullmatch(text) is None:
        raise ValueError(f"{name} must be a valid AuroraSeal identifier")
    return text


def _digest(name: str, value: str) -> str:
    text = str(value or "")
    if _DIGEST_RE.fullmatch(text) is None:
        raise ValueError(f"{name} must match sha256:<64 lowercase hex>")
    return text


def build_approval_requirement(
    *,
    approval_required: bool,
    required_review_level: str,
    required_reviewer_roles: Sequence[str],
    minimum_approver_count: int,
    separation_of_duties: bool,
    escalation_required: bool,
) -> dict[str, Any]:
    if not isinstance(approval_required, bool):
        raise TypeError("approval_required must be bool")
    if required_review_level not in HUMAN_APPROVAL_REVIEW_LEVELS:
        raise ValueError("required_review_level is not supported")
    if not isinstance(minimum_approver_count, int) or isinstance(minimum_approver_count, bool):
        raise TypeError("minimum_approver_count must be int")
    if not 0 <= minimum_approver_count <= 100:
        raise ValueError("minimum_approver_count must be from 0 to 100")
    if not isinstance(separation_of_duties, bool) or not isinstance(escalation_required, bool):
        raise TypeError("separation_of_duties and escalation_required must be bool")
    roles = [str(role) for role in required_reviewer_roles]
    if any(_ROLE_RE.fullmatch(role) is None for role in roles):
        raise ValueError("required_reviewer_roles contains an invalid role")
    if len(roles) != len(set(roles)):
        raise ValueError("required_reviewer_roles must not contain duplicates")
    if roles != sorted(roles):
        raise ValueError("required_reviewer_roles must be lexicographically ascending")
    if approval_required:
        if minimum_approver_count < 1:
            raise ValueError("approval_required requires minimum_approver_count >= 1")
        if not roles:
            raise ValueError("approval_required requires at least one reviewer role")
    else:
        if minimum_approver_count != 0 or roles or escalation_required:
            raise ValueError(
                "approval_required=false requires count=0, roles=[], and escalation_required=false"
            )
    result = {
        "approval_required": approval_required,
        "required_review_level": required_review_level,
        "required_reviewer_roles": roles,
        "minimum_approver_count": minimum_approver_count,
        "separation_of_duties": separation_of_duties,
        "escalation_required": escalation_required,
    }
    canonical_bytes(result)
    return result


def _validated_requirement_mapping(requirement_snapshot: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(requirement_snapshot, Mapping):
        raise TypeError("requirement_snapshot must be a mapping")
    expected = {
        "approval_required",
        "required_review_level",
        "required_reviewer_roles",
        "minimum_approver_count",
        "separation_of_duties",
        "escalation_required",
    }
    actual = set(requirement_snapshot)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ValueError(
            f"requirement_snapshot must contain the exact v1 fields; missing={missing} extra={extra}"
        )
    return build_approval_requirement(
        approval_required=requirement_snapshot["approval_required"],
        required_review_level=requirement_snapshot["required_review_level"],
        required_reviewer_roles=requirement_snapshot["required_reviewer_roles"],
        minimum_approver_count=requirement_snapshot["minimum_approver_count"],
        separation_of_duties=requirement_snapshot["separation_of_duties"],
        escalation_required=requirement_snapshot["escalation_required"],
    )


def build_policy_requirement_binding(
    *,
    policy_id: str,
    policy_version: str,
    policy_digest: str,
    requirement_snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    result = {
        "policy_id": _identifier("policy_id", policy_id),
        "policy_version": str(policy_version),
        "policy_digest": _digest("policy_digest", policy_digest),
        "requirement_snapshot": _validated_requirement_mapping(requirement_snapshot),
    }
    if not result["policy_version"]:
        raise ValueError("policy_version must not be empty")
    canonical_bytes(result)
    return result


class HumanApprovalClient:
    """Programmatic client for AURORA Human Approval D-1 through D-5 surfaces.

    API-key reads identify the organization. Human writes and reviewer
    eligibility additionally require a caller-supplied Supabase access token so
    AURORA can bind the event to the authenticated human principal.
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        reviewer_token: Optional[str] = None,
        timeout: float = 20.0,
        transport=None,
    ) -> None:
        self.transport = transport or HumanApprovalHttpTransport(
            base_url=base_url,
            api_key=api_key,
            timeout=timeout,
        )
        self.reviewer_token = reviewer_token

    def _reviewer_token(self, override: Optional[str]) -> str:
        token = override if override is not None else self.reviewer_token
        if not isinstance(token, str) or not token.strip():
            raise ValueError("reviewer_token is required for this Human Approval operation")
        return token.strip()

    def _json_request(
        self,
        *,
        method: str,
        endpoint: str,
        payload: Optional[Mapping[str, Any]] = None,
        idempotency_key: Optional[str] = None,
        reviewer_token: Optional[str] = None,
        require_reviewer: bool = False,
    ) -> dict[str, Any]:
        body = canonical_bytes(dict(payload)) if payload is not None else b""
        token = self._reviewer_token(reviewer_token) if require_reviewer else None
        response = self.transport.request(
            method=method,
            endpoint=endpoint,
            body=body,
            idempotency_key=idempotency_key,
            reviewer_token=token,
            accept="application/json",
        )
        if response.status < 200 or response.status >= 300:
            _raise_api_error(response)
        value = _json_value(response.body)
        if not isinstance(value, dict):
            raise HumanApprovalTransportError("AURORA returned a non-object JSON response")
        return value

    def list_policy_requirements(self) -> dict[str, Any]:
        return self._json_request(method="GET", endpoint="/v1/approval-policy-requirements")

    def register_policy_requirement(
        self,
        request: Mapping[str, Any],
        *,
        idempotency_key: Optional[str] = None,
        reviewer_token: Optional[str] = None,
    ) -> dict[str, Any]:
        canonical_bytes(dict(request))
        return self._json_request(
            method="POST",
            endpoint="/v1/approval-policy-requirements",
            payload=request,
            idempotency_key=idempotency_key or _idempotency_key("policy-binding"),
            reviewer_token=reviewer_token,
            require_reviewer=True,
        )

    def gate(
        self,
        record_id: str,
        *,
        approval_process_id: Optional[str] = None,
        policy_source_decision_record_id: Optional[str] = None,
    ) -> dict[str, Any]:
        params: dict[str, str] = {}
        if approval_process_id is not None:
            params["approval_process_id"] = approval_process_id
        if policy_source_decision_record_id is not None:
            params["policy_source_decision_record_id"] = policy_source_decision_record_id
        query = f"?{urlencode(params)}" if params else ""
        return self._json_request(
            method="GET",
            endpoint=f"/v1/records/{_identifier('record_id', record_id)}/approval-gate{query}",
        )

    def list_reviews(self, record_id: str) -> dict[str, Any]:
        return self._json_request(
            method="GET",
            endpoint=f"/v1/records/{_identifier('record_id', record_id)}/reviews",
        )

    def process(
        self,
        record_id: str,
        approval_process_id: str,
        *,
        policy_source_decision_record_id: Optional[str] = None,
    ) -> dict[str, Any]:
        params = {}
        if policy_source_decision_record_id is not None:
            params["policy_source_decision_record_id"] = policy_source_decision_record_id
        query = f"?{urlencode(params)}" if params else ""
        return self._json_request(
            method="GET",
            endpoint=(
                f"/v1/records/{_identifier('record_id', record_id)}/approval-processes/"
                f"{_identifier('approval_process_id', approval_process_id)}{query}"
            ),
        )

    def eligibility(
        self,
        record_id: str,
        approval_process_id: str,
        *,
        policy_source_decision_record_id: Optional[str] = None,
        reviewer_token: Optional[str] = None,
    ) -> dict[str, Any]:
        params = {}
        if policy_source_decision_record_id is not None:
            params["policy_source_decision_record_id"] = policy_source_decision_record_id
        query = f"?{urlencode(params)}" if params else ""
        return self._json_request(
            method="GET",
            endpoint=(
                f"/v1/records/{_identifier('record_id', record_id)}/approval-processes/"
                f"{_identifier('approval_process_id', approval_process_id)}/eligibility{query}"
            ),
            reviewer_token=reviewer_token,
            require_reviewer=True,
        )

    def _write(
        self,
        record_id: str,
        action: str,
        request: Mapping[str, Any],
        *,
        idempotency_key: Optional[str],
        reviewer_token: Optional[str],
    ) -> dict[str, Any]:
        canonical_bytes(dict(request))
        return self._json_request(
            method="POST",
            endpoint=f"/v1/records/{_identifier('record_id', record_id)}/{action}",
            payload=request,
            idempotency_key=idempotency_key or _idempotency_key(action),
            reviewer_token=reviewer_token,
            require_reviewer=True,
        )

    def approve(
        self,
        record_id: str,
        request: Mapping[str, Any],
        *,
        idempotency_key: Optional[str] = None,
        reviewer_token: Optional[str] = None,
    ) -> dict[str, Any]:
        return self._write(
            record_id,
            "approve",
            request,
            idempotency_key=idempotency_key,
            reviewer_token=reviewer_token,
        )

    def reject(
        self,
        record_id: str,
        request: Mapping[str, Any],
        *,
        idempotency_key: Optional[str] = None,
        reviewer_token: Optional[str] = None,
    ) -> dict[str, Any]:
        return self._write(
            record_id,
            "reject",
            request,
            idempotency_key=idempotency_key,
            reviewer_token=reviewer_token,
        )

    def review(
        self,
        record_id: str,
        request: Mapping[str, Any],
        *,
        idempotency_key: Optional[str] = None,
        reviewer_token: Optional[str] = None,
    ) -> dict[str, Any]:
        return self._write(
            record_id,
            "reviews",
            request,
            idempotency_key=idempotency_key,
            reviewer_token=reviewer_token,
        )

    def override(
        self,
        record_id: str,
        request: Mapping[str, Any],
        *,
        idempotency_key: Optional[str] = None,
        reviewer_token: Optional[str] = None,
    ) -> dict[str, Any]:
        return self._write(
            record_id,
            "override",
            request,
            idempotency_key=idempotency_key,
            reviewer_token=reviewer_token,
        )

    def escalate(
        self,
        record_id: str,
        request: Mapping[str, Any],
        *,
        idempotency_key: Optional[str] = None,
        reviewer_token: Optional[str] = None,
    ) -> dict[str, Any]:
        return self._write(
            record_id,
            "escalate",
            request,
            idempotency_key=idempotency_key,
            reviewer_token=reviewer_token,
        )

    def defer(
        self,
        record_id: str,
        request: Mapping[str, Any],
        *,
        idempotency_key: Optional[str] = None,
        reviewer_token: Optional[str] = None,
    ) -> dict[str, Any]:
        return self._write(
            record_id,
            "defer",
            request,
            idempotency_key=idempotency_key,
            reviewer_token=reviewer_token,
        )

    def approve_from_eligibility(
        self,
        record_id: str,
        eligibility: Mapping[str, Any],
        *,
        reason_code: str,
        reason: str,
        execution_authorization_granted: bool = False,
        idempotency_key: Optional[str] = None,
        reviewer_token: Optional[str] = None,
    ) -> dict[str, Any]:
        if eligibility.get("eligible_to_count") is not True:
            reasons = eligibility.get("ineligibility_reasons") or []
            raise ValueError(f"reviewer is not eligible to count: {reasons}")
        submission = eligibility.get("approval_submission")
        if not isinstance(submission, Mapping):
            raise ValueError("eligibility response does not contain approval_submission")
        request = dict(submission)
        request.update(
            {
                "reason_code": str(reason_code),
                "reason": str(reason),
                "policy_acknowledged": True,
                "execution_authorization_granted": bool(execution_authorization_granted),
            }
        )
        return self.approve(
            record_id,
            request,
            idempotency_key=idempotency_key,
            reviewer_token=reviewer_token,
        )


__all__ = [
    "HUMAN_APPROVAL_DECLARED_STATES",
    "HUMAN_APPROVAL_PROFILE_ID",
    "HUMAN_APPROVAL_PROFILE_VERSION",
    "HUMAN_APPROVAL_REVIEW_LEVELS",
    "HumanApprovalAPIError",
    "HumanApprovalClient",
    "HumanApprovalHttpResponse",
    "HumanApprovalHttpTransport",
    "HumanApprovalTransportError",
    "build_approval_requirement",
    "build_policy_requirement_binding",
]