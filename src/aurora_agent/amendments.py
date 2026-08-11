from __future__ import annotations

import json
import os
import re
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .canonical import canonical_bytes
from .exceptions import AuroraAgentError

AMENDMENT_PROFILE_ID = "auroraseal.amendment"
AMENDMENT_PROFILE_VERSION = "1.0"
AMENDMENT_TYPES = (
    "amendment",
    "correction",
    "supersession",
    "reversal",
    "withdrawal",
)
LIFECYCLE_ROLES = ("CURRENT", "HISTORICAL", "PENDING_SUCCESSOR")
LIFECYCLE_EFFECTS = (
    "active",
    "amended",
    "corrected",
    "reversed",
    "superseded",
    "withdrawn",
    "pending_activation",
)

_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_REASON_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")
_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$")


class AmendmentTransportError(AuroraAgentError):
    """Network or protocol failure before a valid AURORA response is available."""


class AmendmentAPIError(AuroraAgentError):
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
class AmendmentHttpResponse:
    status: int
    body: bytes
    headers: Mapping[str, str]


class AmendmentHttpTransport:
    """In-memory API-key transport for Amendment / lifecycle operations."""

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
        accept: str = "application/json",
    ) -> AmendmentHttpResponse:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": accept,
        }
        if body:
            headers["Content-Type"] = "application/json"
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        request = Request(
            self.base_url + endpoint,
            data=body if method not in {"GET", "HEAD"} and body else None,
            method=method,
            headers=headers,
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return AmendmentHttpResponse(
                    status=int(response.status),
                    body=response.read(),
                    headers=dict(response.headers.items()),
                )
        except HTTPError as exc:
            return AmendmentHttpResponse(
                status=int(exc.code),
                body=exc.read(),
                headers=dict(exc.headers.items()),
            )
        except URLError as exc:
            raise AmendmentTransportError(str(exc.reason)) from exc
        except OSError as exc:
            raise AmendmentTransportError(str(exc)) from exc


def _json_value(raw: bytes) -> Any:
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AmendmentTransportError("AURORA returned invalid JSON") from exc


def _raise_api_error(response: AmendmentHttpResponse) -> None:
    try:
        payload = _json_value(response.body)
    except AmendmentTransportError:
        text = response.body.decode("utf-8", errors="replace")[:500]
        raise AmendmentAPIError(
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
    raise AmendmentAPIError(
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


def _timestamp(name: str, value: str) -> str:
    text = str(value or "")
    if _TIMESTAMP_RE.fullmatch(text) is None:
        raise ValueError(f"{name} must use YYYY-MM-DDTHH:MM:SS.ffffffZ")
    return text


def _idempotency_key(operation: str) -> str:
    return f"auroraseal.amendment.{operation}:{uuid.uuid4().hex}"


def build_amendment_request(
    *,
    amendment_type: str,
    target_record_id: str,
    reason_code: str,
    reason: str,
    occurred_at: str,
    actor: Mapping[str, Any],
    privacy: Mapping[str, Any],
    expected_current_record_id: str,
    expected_current_record_digest: str,
    successor_record_id: Optional[str] = None,
    expected_previous_amendment_digest: Optional[str] = None,
    capture_mode: str = "DIGEST_ONLY",
    evidence_references: tuple[Mapping[str, Any], ...] | list[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Build the exact coordinated Stage E write request.

    The caller must supply the current head observed from ``lifecycle()``. The
    SDK deliberately does not infer or advance chain state locally.
    """
    if amendment_type not in AMENDMENT_TYPES:
        raise ValueError(f"amendment_type must be one of {AMENDMENT_TYPES}")
    target_record_id = _identifier("target_record_id", target_record_id)
    expected_current_record_id = _identifier(
        "expected_current_record_id", expected_current_record_id
    )
    if target_record_id != expected_current_record_id:
        raise ValueError("target_record_id must equal expected_current_record_id")

    successor_required = amendment_type in {"amendment", "correction", "supersession"}
    if successor_required and successor_record_id is None:
        raise ValueError(f"{amendment_type} requires successor_record_id")
    if amendment_type == "withdrawal" and successor_record_id is not None:
        raise ValueError("withdrawal forbids successor_record_id")

    if not isinstance(reason_code, str) or _REASON_CODE_RE.fullmatch(reason_code) is None:
        raise ValueError("reason_code must match ^[a-z][a-z0-9_]{1,63}$")
    if not isinstance(reason, str) or not (1 <= len(reason) <= 4000):
        raise ValueError("reason must contain 1-4000 characters")
    if capture_mode not in {"DIGEST_ONLY", "REDACTED", "FULL_PAYLOAD"}:
        raise ValueError("capture_mode is unsupported")
    if not isinstance(actor, Mapping) or not actor:
        raise ValueError("actor must be a non-empty mapping")
    if not isinstance(privacy, Mapping) or not privacy:
        raise ValueError("privacy must be a non-empty mapping")

    request: dict[str, Any] = {
        "amendment_type": amendment_type,
        "target_record_id": target_record_id,
        "reason_code": reason_code,
        "reason": reason,
        "occurred_at": _timestamp("occurred_at", occurred_at),
        "capture_mode": capture_mode,
        "actor": dict(actor),
        "privacy": dict(privacy),
        "evidence_references": [dict(item) for item in evidence_references],
        "expected_current_record_id": expected_current_record_id,
        "expected_current_record_digest": _digest(
            "expected_current_record_digest", expected_current_record_digest
        ),
    }
    if successor_record_id is not None:
        request["successor_record_id"] = _identifier(
            "successor_record_id", successor_record_id
        )
    if expected_previous_amendment_digest is not None:
        request["expected_previous_amendment_digest"] = _digest(
            "expected_previous_amendment_digest",
            expected_previous_amendment_digest,
        )

    canonical_bytes(request)
    return request


def build_amendment_request_from_lifecycle(
    lifecycle: Mapping[str, Any],
    *,
    amendment_type: str,
    reason_code: str,
    reason: str,
    occurred_at: str,
    actor: Mapping[str, Any],
    privacy: Mapping[str, Any],
    successor_record_id: Optional[str] = None,
    capture_mode: str = "DIGEST_ONLY",
    evidence_references: tuple[Mapping[str, Any], ...] | list[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Use the exact server-returned expected head as the write template."""
    if not isinstance(lifecycle, Mapping):
        raise ValueError("lifecycle must be a mapping")
    expected = lifecycle.get("expected_head")
    if not isinstance(expected, Mapping):
        raise ValueError("lifecycle.expected_head is missing")
    current_id = expected.get("expected_current_record_id")
    current_digest = expected.get("expected_current_record_digest")
    if not current_id or not current_digest:
        raise ValueError("lifecycle has no current operational record")

    return build_amendment_request(
        amendment_type=amendment_type,
        target_record_id=str(current_id),
        reason_code=reason_code,
        reason=reason,
        occurred_at=occurred_at,
        actor=actor,
        privacy=privacy,
        expected_current_record_id=str(current_id),
        expected_current_record_digest=str(current_digest),
        successor_record_id=successor_record_id,
        expected_previous_amendment_digest=expected.get(
            "expected_previous_amendment_digest"
        ),
        capture_mode=capture_mode,
        evidence_references=evidence_references,
    )


class AmendmentClient:
    """API-key client for AuroraSeal Amendment v1 lifecycle operations."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        timeout: float = 20.0,
        transport=None,
    ) -> None:
        self.transport = transport or AmendmentHttpTransport(
            base_url=base_url,
            api_key=api_key,
            timeout=timeout,
        )

    def _json_request(
        self,
        *,
        method: str,
        endpoint: str,
        payload: Optional[Mapping[str, Any]] = None,
        idempotency_key: Optional[str] = None,
    ) -> dict[str, Any]:
        body = canonical_bytes(dict(payload)) if payload is not None else b""
        response = self.transport.request(
            method=method,
            endpoint=endpoint,
            body=body,
            idempotency_key=idempotency_key,
            accept="application/json",
        )
        if response.status < 200 or response.status >= 300:
            _raise_api_error(response)
        value = _json_value(response.body)
        if not isinstance(value, dict):
            raise AmendmentTransportError("AURORA returned a non-object JSON response")
        return value

    def lifecycle(self, record_id: str) -> dict[str, Any]:
        return self._json_request(
            method="GET",
            endpoint=f"/v1/records/{_identifier('record_id', record_id)}/lifecycle",
        )

    def get_amendment(self, record_id: str) -> dict[str, Any]:
        return self._json_request(
            method="GET",
            endpoint=f"/v1/amendments/{_identifier('record_id', record_id)}",
        )

    def prepare_ai_output_successor(
        self,
        target_record_id: str,
        successor_request: Mapping[str, Any],
        *,
        idempotency_key: Optional[str] = None,
    ) -> dict[str, Any]:
        payload = {
            "target_record_id": _identifier("target_record_id", target_record_id),
            "successor": dict(successor_request),
        }
        canonical_bytes(payload)
        return self._json_request(
            method="POST",
            endpoint="/v1/amendments/successors/ai-output",
            payload=payload,
            idempotency_key=idempotency_key or _idempotency_key("prepare-ai-output-successor"),
        )

    def prepare_ai_decision_successor(
        self,
        target_record_id: str,
        successor_request: Mapping[str, Any],
        *,
        idempotency_key: Optional[str] = None,
    ) -> dict[str, Any]:
        payload = {
            "target_record_id": _identifier("target_record_id", target_record_id),
            "successor": dict(successor_request),
        }
        canonical_bytes(payload)
        return self._json_request(
            method="POST",
            endpoint="/v1/amendments/successors/ai-decision",
            payload=payload,
            idempotency_key=idempotency_key or _idempotency_key("prepare-ai-decision-successor"),
        )

    def seal_amendment(
        self,
        request: Mapping[str, Any],
        *,
        idempotency_key: Optional[str] = None,
    ) -> dict[str, Any]:
        canonical_bytes(dict(request))
        return self._json_request(
            method="POST",
            endpoint="/v1/amendments",
            payload=request,
            idempotency_key=idempotency_key or _idempotency_key("seal"),
        )

    def download_lifecycle_bundle(
        self,
        record_id: str,
        destination: str | Path,
    ) -> Path:
        record_id = _identifier("record_id", record_id)
        response = self.transport.request(
            method="GET",
            endpoint=f"/v1/records/{record_id}/lifecycle-bundle",
            body=b"",
            idempotency_key=None,
            accept="application/zip",
        )
        if response.status < 200 or response.status >= 300:
            _raise_api_error(response)
        if not response.body.startswith(b"PK"):
            raise AmendmentTransportError("AURORA returned a non-ZIP lifecycle bundle")

        target = Path(destination)
        if target.exists() and target.is_dir():
            target = target / f"AuroraSeal_Lifecycle_{record_id}.zip"
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=target.name + ".", dir=str(target.parent))
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(response.body)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, target)
        except Exception:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise
        return target


__all__ = [
    "AMENDMENT_PROFILE_ID",
    "AMENDMENT_PROFILE_VERSION",
    "AMENDMENT_TYPES",
    "LIFECYCLE_EFFECTS",
    "LIFECYCLE_ROLES",
    "AmendmentAPIError",
    "AmendmentClient",
    "AmendmentHttpResponse",
    "AmendmentHttpTransport",
    "AmendmentTransportError",
    "build_amendment_request",
    "build_amendment_request_from_lifecycle",
]