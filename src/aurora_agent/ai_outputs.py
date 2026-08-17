from __future__ import annotations

import json
import os
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .canonical import canonical_bytes, commitment
from .exceptions import AuroraAgentError, CanonicalizationError


AI_OUTPUT_SCHEMA_ID = "auroraseal.evidence"
AI_OUTPUT_SCHEMA_VERSION = "3.0"
AI_OUTPUT_PROFILE_ID = "auroraseal.ai_output"
AI_OUTPUT_PROFILE_VERSION = "1.0"
AI_OUTPUT_CAPTURE_MODES = ("DIGEST_ONLY", "REDACTED", "FULL_PAYLOAD")
AI_OUTPUT_FORMATS = (
    "text",
    "structured_json",
    "classification",
    "ranking",
    "code",
    "embedding_reference",
    "tool_plan",
    "multimodal_reference",
    "other",
)


class AIOutputTransportError(AuroraAgentError):
    """Network or protocol failure before a valid AURORA response is available."""


class AIOutputAPIError(AuroraAgentError):
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
        self.code = code
        self.detail = detail
        self.retry_permitted = bool(retry_permitted)


@dataclass(frozen=True)
class AIOutputHttpResponse:
    status: int
    body: bytes
    headers: Mapping[str, str]


class AIOutputHttpTransport:
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
    ) -> AIOutputHttpResponse:
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
                return AIOutputHttpResponse(
                    status=int(response.status),
                    body=response.read(),
                    headers=dict(response.headers.items()),
                )
        except HTTPError as exc:
            return AIOutputHttpResponse(
                status=int(exc.code),
                body=exc.read(),
                headers=dict(exc.headers.items()),
            )
        except URLError as exc:
            raise AIOutputTransportError(str(exc.reason)) from exc
        except OSError as exc:
            raise AIOutputTransportError(str(exc)) from exc


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _idempotency_key(operation: str) -> str:
    return f"auroraseal.ai-output.{operation}:{uuid.uuid4().hex}"


def _json_object(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AIOutputTransportError("AURORA returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise AIOutputTransportError("AURORA returned a non-object JSON response")
    return value


def _raise_api_error(response: AIOutputHttpResponse) -> None:
    try:
        payload = _json_object(response.body)
    except AIOutputTransportError:
        text = response.body.decode("utf-8", errors="replace")[:500]
        raise AIOutputAPIError(
            status=response.status,
            code="HTTP_ERROR",
            detail=text or "AURORA request failed",
        )
    error = payload.get("error")
    if isinstance(error, dict):
        code = str(error.get("code") or "HTTP_ERROR")
        detail = str(error.get("detail") or "AURORA request failed")
        retry = bool(error.get("retry_permitted"))
    else:
        code = "HTTP_ERROR"
        detail = str(payload.get("detail") or payload.get("message") or "AURORA request failed")
        retry = False
    raise AIOutputAPIError(
        status=response.status,
        code=code,
        detail=detail,
        retry_permitted=retry,
    )


def _actor(actor: Mapping[str, Any]) -> dict[str, Any]:
    value = dict(actor)
    if not value.get("actor_type") or not value.get("actor_id"):
        raise ValueError("actor requires actor_type and actor_id")
    canonical_bytes(value)
    return value


def _privacy(privacy: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    value = dict(
        privacy
        or {
            "contains_personal_data": False,
            "redaction_status": "not_applicable",
            "legal_hold_status": "not_applicable",
            "public_display_mode": "metadata_only",
        }
    )
    canonical_bytes(value)
    return value


def content_digest(value: Any) -> str:
    """Return the exact AuroraSeal content commitment for JSON-compatible data."""

    return commitment(value)


class AIOutputClient:
    """API-key client for first-class AuroraSeal AI Output v3 records.

    The API key remains in the in-memory transport. This client does not write
    credentials or request payloads to a local outbox.
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        timeout: float = 20.0,
        transport=None,
    ) -> None:
        self.transport = transport or AIOutputHttpTransport(
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
        allow_list: bool = False,
    ) -> Any:
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
        if allow_list:
            try:
                value = json.loads(response.body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise AIOutputTransportError("AURORA returned invalid JSON") from exc
            return value
        return _json_object(response.body)

    def create(
        self,
        request: Mapping[str, Any],
        *,
        idempotency_key: Optional[str] = None,
    ) -> dict[str, Any]:
        return self._json_request(
            method="POST",
            endpoint="/v1/ai-outputs",
            payload=request,
            idempotency_key=idempotency_key or _idempotency_key("create"),
        )

    def create_digest_only(
        self,
        *,
        input_digest: str,
        output_digest: str,
        model_provider: str,
        model_name: str,
        actor: Mapping[str, Any],
        output_format: str = "text",
        generated_at: Optional[str] = None,
        privacy: Optional[Mapping[str, Any]] = None,
        idempotency_key: Optional[str] = None,
        **metadata: Any,
    ) -> dict[str, Any]:
        payload = {
            "output_format": output_format,
            "capture_mode": "DIGEST_ONLY",
            "actor": _actor(actor),
            "privacy": _privacy(privacy),
            "input_digest": input_digest,
            "output_digest": output_digest,
            "model_provider": model_provider,
            "model_name": model_name,
            "generated_at": generated_at or _utc_now(),
            **metadata,
        }
        return self.create(payload, idempotency_key=idempotency_key)

    def create_full_payload(
        self,
        *,
        input_content: Any,
        output_content: Any,
        model_provider: str,
        model_name: str,
        actor: Mapping[str, Any],
        output_format: str = "text",
        generated_at: Optional[str] = None,
        privacy: Optional[Mapping[str, Any]] = None,
        idempotency_key: Optional[str] = None,
        **metadata: Any,
    ) -> dict[str, Any]:
        # Validate before transmission. Floats and unsupported types are rejected.
        canonical_bytes(input_content)
        canonical_bytes(output_content)
        payload = {
            "output_format": output_format,
            "capture_mode": "FULL_PAYLOAD",
            "actor": _actor(actor),
            "privacy": _privacy(privacy),
            "input_content": input_content,
            "output_content": output_content,
            "model_provider": model_provider,
            "model_name": model_name,
            "generated_at": generated_at or _utc_now(),
            **metadata,
        }
        return self.create(payload, idempotency_key=idempotency_key)

    def create_redacted(
        self,
        *,
        input_digest: str,
        output_digest: str,
        redacted_input: Any,
        redacted_output: Any,
        model_provider: str,
        model_name: str,
        actor: Mapping[str, Any],
        output_format: str = "text",
        generated_at: Optional[str] = None,
        privacy: Optional[Mapping[str, Any]] = None,
        idempotency_key: Optional[str] = None,
        **metadata: Any,
    ) -> dict[str, Any]:
        canonical_bytes(redacted_input)
        canonical_bytes(redacted_output)
        privacy_value = _privacy(
            privacy
            or {
                "contains_personal_data": True,
                "redaction_status": "partially_redacted",
                "legal_hold_status": "not_applicable",
                "public_display_mode": "redacted",
            }
        )
        payload = {
            "output_format": output_format,
            "capture_mode": "REDACTED",
            "actor": _actor(actor),
            "privacy": privacy_value,
            "input_content": redacted_input,
            "output_content": redacted_output,
            "input_digest": input_digest,
            "output_digest": output_digest,
            "model_provider": model_provider,
            "model_name": model_name,
            "generated_at": generated_at or _utc_now(),
            **metadata,
        }
        return self.create(payload, idempotency_key=idempotency_key)

    def get(self, record_id: str) -> dict[str, Any]:
        return self._json_request(
            method="GET",
            endpoint=f"/v1/ai-outputs/{record_id}",
        )

    def seal(
        self,
        record_id: str,
        *,
        idempotency_key: Optional[str] = None,
    ) -> dict[str, Any]:
        return self._json_request(
            method="POST",
            endpoint=f"/v1/ai-outputs/{record_id}/seal",
            idempotency_key=idempotency_key or _idempotency_key("seal"),
        )

    def seal_standard(
        self,
        record_id: str,
        *,
        idempotency_key: Optional[str] = None,
    ) -> dict[str, Any]:
        """Admit a DIGESTED AI Output to Standard batch anchoring."""

        return self._json_request(
            method="POST",
            endpoint=f"/v1/ai-outputs/{record_id}/seal-standard",
            idempotency_key=idempotency_key or _idempotency_key("seal-standard"),
        )

    def verify(self, record_id: str) -> dict[str, Any]:
        return self._json_request(
            method="POST",
            endpoint=f"/v1/ai-outputs/{record_id}/verify",
        )

    def link_decision(
        self,
        ai_output_record_id: str,
        decision_record_id: str,
        *,
        idempotency_key: Optional[str] = None,
    ) -> dict[str, Any]:
        """Create one immutable AURORA-signed output-to-decision relationship proof."""

        return self._json_request(
            method="POST",
            endpoint="/v1/ai-output-decision-links",
            payload={
                "ai_output_record_id": ai_output_record_id,
                "decision_record_id": decision_record_id,
            },
            idempotency_key=idempotency_key or _idempotency_key("link-decision"),
        )

    def get_relationship(self, link_id: str) -> dict[str, Any]:
        return self._json_request(
            method="GET",
            endpoint=f"/v1/ai-output-decision-links/{link_id}",
        )

    def verify_relationship(self, link_id: str) -> dict[str, Any]:
        return self._json_request(
            method="POST",
            endpoint=f"/v1/ai-output-decision-links/{link_id}/verify",
        )

    def list_linked_decisions(self, ai_output_record_id: str) -> list[dict[str, Any]]:
        value = self._json_request(
            method="GET",
            endpoint=f"/v1/ai-outputs/{ai_output_record_id}/decisions",
            allow_list=True,
        )
        if not isinstance(value, list):
            raise AIOutputTransportError("AURORA returned a non-list relationship response")
        return value

    def list_decision_outputs(self, decision_record_id: str) -> list[dict[str, Any]]:
        value = self._json_request(
            method="GET",
            endpoint=f"/v1/audit-records/{decision_record_id}/ai-outputs",
            allow_list=True,
        )
        if not isinstance(value, list):
            raise AIOutputTransportError("AURORA returned a non-list relationship response")
        return value

    def download_bundle(self, record_id: str, destination: str | Path) -> Path:
        response = self.transport.request(
            method="GET",
            endpoint=f"/v1/ai-outputs/{record_id}/bundle",
            body=b"",
            idempotency_key=None,
            accept="application/zip",
        )
        if response.status < 200 or response.status >= 300:
            _raise_api_error(response)
        if not response.body.startswith(b"PK"):
            raise AIOutputTransportError("AURORA returned a non-ZIP bundle")

        target = Path(destination)
        if target.exists() and target.is_dir():
            target = target / f"AuroraSeal_AIOutput_{record_id}.zip"
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
    "AI_OUTPUT_CAPTURE_MODES",
    "AI_OUTPUT_FORMATS",
    "AI_OUTPUT_PROFILE_ID",
    "AI_OUTPUT_PROFILE_VERSION",
    "AI_OUTPUT_SCHEMA_ID",
    "AI_OUTPUT_SCHEMA_VERSION",
    "AIOutputAPIError",
    "AIOutputClient",
    "AIOutputHttpResponse",
    "AIOutputHttpTransport",
    "AIOutputTransportError",
    "content_digest",
]