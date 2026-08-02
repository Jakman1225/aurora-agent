from __future__ import annotations

import json
import os
import re
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .canonical import canonical_bytes
from .exceptions import AuroraAgentError


AI_DECISION_SCHEMA_ID = "auroraseal.evidence"
AI_DECISION_SCHEMA_VERSION = "3.0"
AI_DECISION_PROFILE_ID = "auroraseal.ai_decision"
AI_DECISION_PROFILE_VERSION = "1.0"
AI_DECISION_CAPTURE_MODES = ("DIGEST_ONLY", "REDACTED", "FULL_PAYLOAD")
AI_DECISION_EVIDENCE_COMPLETENESS = (
    "complete",
    "partial",
    "unknown",
    "not_applicable",
)
AI_DECISION_SCORE_SCALE_KINDS = (
    "bounded_numeric",
    "unbounded_numeric",
    "ordinal",
    "categorical",
)
AI_DECISION_SCORE_TRANSFORMS = (
    "identity",
    "probability",
    "percentage",
    "log_odds",
    "z_score",
    "provider_defined",
)
AI_DECISION_SCORE_DIRECTIONS = (
    "higher_is_favorable",
    "higher_is_riskier",
    "non_monotonic",
    "not_applicable",
)

_CANONICAL_DECIMAL_RE = re.compile(
    r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]*[1-9])?$"
)
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_TIMESTAMP_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$"
)


class AIDecisionTransportError(AuroraAgentError):
    """Network or protocol failure before a valid AURORA response is available."""


class AIDecisionAPIError(AuroraAgentError):
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
class AIDecisionHttpResponse:
    status: int
    body: bytes
    headers: Mapping[str, str]


class AIDecisionHttpTransport:
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
    ) -> AIDecisionHttpResponse:
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
                return AIDecisionHttpResponse(
                    status=int(response.status),
                    body=response.read(),
                    headers=dict(response.headers.items()),
                )
        except HTTPError as exc:
            return AIDecisionHttpResponse(
                status=int(exc.code),
                body=exc.read(),
                headers=dict(exc.headers.items()),
            )
        except URLError as exc:
            raise AIDecisionTransportError(str(exc.reason)) from exc
        except OSError as exc:
            raise AIDecisionTransportError(str(exc)) from exc


def _idempotency_key(operation: str) -> str:
    return f"auroraseal.ai-decision.{operation}:{uuid.uuid4().hex}"


def _json_value(raw: bytes) -> Any:
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AIDecisionTransportError("AURORA returned invalid JSON") from exc


def _json_object(raw: bytes) -> dict[str, Any]:
    value = _json_value(raw)
    if not isinstance(value, dict):
        raise AIDecisionTransportError("AURORA returned a non-object JSON response")
    return value


def _raise_api_error(response: AIDecisionHttpResponse) -> None:
    try:
        payload = _json_object(response.body)
    except AIDecisionTransportError:
        text = response.body.decode("utf-8", errors="replace")[:500]
        raise AIDecisionAPIError(
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
    raise AIDecisionAPIError(
        status=response.status,
        code=code,
        detail=detail,
        retry_permitted=retry,
    )


def canonical_decimal(value: str | int | Decimal) -> str:
    """Return a canonical decimal string and reject floats or exponent notation."""

    if isinstance(value, bool) or isinstance(value, float):
        raise ValueError("decimal values must be strings, integers, or Decimal values; floats are forbidden")
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("decimal value must be finite")
        text = format(value, "f")
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        if text in {"-0", ""}:
            text = "0"
    else:
        text = str(value)
    if _CANONICAL_DECIMAL_RE.fullmatch(text) is None:
        raise ValueError("decimal value must use canonical non-exponent notation")
    return text


def _identifier(name: str, value: str) -> str:
    text = str(value)
    if _IDENTIFIER_RE.fullmatch(text) is None:
        raise ValueError(f"{name} must be a valid AuroraSeal identifier")
    return text


def _digest(name: str, value: str) -> str:
    text = str(value)
    if _DIGEST_RE.fullmatch(text) is None:
        raise ValueError(f"{name} must match sha256:<64 lowercase hex>")
    return text


def _timestamp(name: str, value: str) -> str:
    text = str(value)
    if _TIMESTAMP_RE.fullmatch(text) is None:
        raise ValueError(f"{name} must use YYYY-MM-DDTHH:MM:SS.ffffffZ")
    try:
        datetime.strptime(text, "%Y-%m-%dT%H:%M:%S.%fZ")
    except ValueError as exc:
        raise ValueError(f"{name} is not a valid UTC timestamp") from exc
    return text


def _unique_identifiers(name: str, values: Sequence[str]) -> list[str]:
    result = [_identifier(name, item) for item in values]
    if len(result) != len(set(result)):
        raise ValueError(f"{name} must contain unique values")
    return result


def build_score_interpretation(
    *,
    score_value: str | int | Decimal,
    scale_kind: str,
    score_direction: str,
    score_source: str,
    transform: str = "identity",
    minimum: str | int | Decimal | None = None,
    maximum: str | int | Decimal | None = None,
    unit: Optional[str] = None,
    labels: Sequence[str] = (),
    provider_scale_reference: Optional[str] = None,
    threshold_value: str | int | Decimal | None = None,
    threshold_meaning: Optional[str] = None,
    risk_band: Optional[str] = None,
) -> dict[str, Any]:
    if scale_kind not in AI_DECISION_SCORE_SCALE_KINDS:
        raise ValueError("unsupported score scale kind")
    if transform not in AI_DECISION_SCORE_TRANSFORMS:
        raise ValueError("unsupported score transform")
    if score_direction not in AI_DECISION_SCORE_DIRECTIONS:
        raise ValueError("unsupported score direction")
    score_source_text = str(score_source)
    if not score_source_text.strip():
        raise ValueError("score_source must not be empty")

    minimum_text = canonical_decimal(minimum) if minimum is not None else None
    maximum_text = canonical_decimal(maximum) if maximum is not None else None
    label_values = [str(item) for item in labels]
    if any(not item.strip() for item in label_values):
        raise ValueError("score labels cannot be empty")
    if len(label_values) != len(set(label_values)):
        raise ValueError("score labels must be unique")

    numeric = scale_kind in {"bounded_numeric", "unbounded_numeric"}
    if scale_kind == "bounded_numeric":
        if minimum_text is None or maximum_text is None:
            raise ValueError("bounded_numeric requires minimum and maximum")
        if Decimal(minimum_text) >= Decimal(maximum_text):
            raise ValueError("score scale minimum must be less than maximum")
        if label_values:
            raise ValueError("numeric score scales cannot declare labels")
    elif scale_kind == "unbounded_numeric":
        if minimum_text is not None or maximum_text is not None or label_values:
            raise ValueError("unbounded_numeric cannot declare bounds or labels")
    else:
        if minimum_text is not None or maximum_text is not None:
            raise ValueError("ordinal and categorical scales cannot declare numeric bounds")
        if not label_values:
            raise ValueError("ordinal and categorical scales require labels")

    if transform == "probability" and not (
        scale_kind == "bounded_numeric" and minimum_text == "0" and maximum_text == "1"
    ):
        raise ValueError("probability requires bounded_numeric scale 0..1")
    if transform == "percentage" and not (
        scale_kind == "bounded_numeric" and minimum_text == "0" and maximum_text == "100"
    ):
        raise ValueError("percentage requires bounded_numeric scale 0..100")
    if transform == "log_odds" and scale_kind != "unbounded_numeric":
        raise ValueError("log_odds requires unbounded_numeric scale")
    if transform == "z_score" and not numeric:
        raise ValueError("z_score requires a numeric scale")

    provider_reference_text = None
    if provider_scale_reference is not None:
        provider_reference_text = str(provider_scale_reference)
        if not provider_reference_text.strip():
            raise ValueError("provider_scale_reference must not be empty")
    if transform == "provider_defined" and provider_reference_text is None:
        raise ValueError("provider_defined requires provider_scale_reference")

    if (threshold_value is None) != (threshold_meaning is None):
        raise ValueError("threshold_value and threshold_meaning must be declared together")
    threshold_meaning_text = None
    if threshold_meaning is not None:
        threshold_meaning_text = str(threshold_meaning)
        if not threshold_meaning_text.strip():
            raise ValueError("threshold_meaning must not be empty")

    if numeric:
        score_value_text = canonical_decimal(score_value)
        threshold_value_text = (
            canonical_decimal(threshold_value) if threshold_value is not None else None
        )
        if scale_kind == "bounded_numeric":
            minimum_decimal = Decimal(minimum_text)
            maximum_decimal = Decimal(maximum_text)
            score_decimal = Decimal(score_value_text)
            if score_decimal < minimum_decimal or score_decimal > maximum_decimal:
                raise ValueError("score_value is outside the declared score scale")
            if threshold_value_text is not None:
                threshold_decimal = Decimal(threshold_value_text)
                if threshold_decimal < minimum_decimal or threshold_decimal > maximum_decimal:
                    raise ValueError("threshold_value is outside the declared score scale")
    else:
        if not isinstance(score_value, str):
            raise ValueError("ordinal and categorical score values must be strings")
        score_value_text = score_value
        if score_value_text not in label_values:
            raise ValueError("score_value is not in the declared score scale labels")
        if threshold_value is not None or threshold_meaning is not None:
            raise ValueError("ordinal and categorical score scales do not accept thresholds")
        threshold_value_text = None

    score_scale: dict[str, Any] = {
        "kind": scale_kind,
        "transform": transform,
        "labels": label_values,
    }
    if minimum_text is not None:
        score_scale["minimum"] = minimum_text
    if maximum_text is not None:
        score_scale["maximum"] = maximum_text
    if unit is not None:
        unit_text = str(unit)
        if not unit_text.strip():
            raise ValueError("unit must not be empty")
        score_scale["unit"] = unit_text
    if provider_reference_text is not None:
        score_scale["provider_scale_reference"] = provider_reference_text

    result: dict[str, Any] = {
        "score_value": score_value_text,
        "score_scale": score_scale,
        "score_direction": score_direction,
        "score_source": score_source_text,
    }
    if threshold_value_text is not None:
        result["threshold_value"] = threshold_value_text
        result["threshold_meaning"] = threshold_meaning_text
    if risk_band is not None:
        risk_band_text = str(risk_band)
        if not risk_band_text.strip():
            raise ValueError("risk_band must not be empty")
        result["risk_band"] = risk_band_text
    canonical_bytes(result)
    return result


def build_policy_context(
    *,
    policy_id: str,
    policy_name: str,
    policy_version: str,
    policy_digest: str,
    effective_at: str,
    policy_sections: Sequence[str] = (),
    capture_mode: str = "DIGEST_ONLY",
    policy_snapshot_reference: Optional[str] = None,
    organization_policy_reference: Optional[str] = None,
) -> dict[str, Any]:
    if capture_mode not in AI_DECISION_CAPTURE_MODES:
        raise ValueError("unsupported policy capture_mode")
    if capture_mode == "FULL_PAYLOAD" and not policy_snapshot_reference:
        raise ValueError("FULL_PAYLOAD policy context requires policy_snapshot_reference")
    sections = [str(item) for item in policy_sections]
    if len(sections) != len(set(sections)):
        raise ValueError("policy_sections must contain unique values")
    result: dict[str, Any] = {
        "policy_id": _identifier("policy_id", policy_id),
        "policy_name": str(policy_name),
        "policy_version": str(policy_version),
        "policy_digest": _digest("policy_digest", policy_digest),
        "policy_sections": sections,
        "capture_mode": capture_mode,
        "effective_at": _timestamp("effective_at", effective_at),
    }
    if policy_snapshot_reference is not None:
        result["policy_snapshot_reference"] = str(policy_snapshot_reference)
    if organization_policy_reference is not None:
        result["organization_policy_reference"] = str(organization_policy_reference)
    canonical_bytes(result)
    return result


def build_evidence_flag(
    *,
    code: str,
    description: str,
    evidence_ids: Sequence[str] = (),
) -> dict[str, Any]:
    result = {
        "code": _identifier("code", code),
        "description": str(description),
        "evidence_ids": _unique_identifiers("evidence_ids", evidence_ids),
    }
    canonical_bytes(result)
    return result


def build_evidence_assessment(
    *,
    evidence_completeness: str,
    missing_evidence_flags: Sequence[Mapping[str, Any]] = (),
    uncertainty_flags: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    if evidence_completeness not in AI_DECISION_EVIDENCE_COMPLETENESS:
        raise ValueError("unsupported evidence completeness value")
    missing = [dict(item) for item in missing_evidence_flags]
    uncertainty = [dict(item) for item in uncertainty_flags]
    if evidence_completeness == "complete" and missing:
        raise ValueError("complete evidence cannot declare missing evidence flags")
    if evidence_completeness == "partial" and not (missing or uncertainty):
        raise ValueError("partial evidence requires a missing-evidence or uncertainty flag")
    if evidence_completeness == "not_applicable" and (missing or uncertainty):
        raise ValueError("not_applicable evidence cannot declare evidence flags")
    result = {
        "evidence_completeness": evidence_completeness,
        "missing_evidence_flags": missing,
        "uncertainty_flags": uncertainty,
    }
    canonical_bytes(result)
    return result


def build_ai_decision_request(
    *,
    decision_type: str,
    declared_outcome: str,
    decision_reason: str,
    decided_at: str,
    evidence_assessment: Mapping[str, Any],
    actor: Mapping[str, Any],
    privacy: Mapping[str, Any],
    capture_mode: str = "DIGEST_ONLY",
    decision_id: Optional[str] = None,
    operator_id: Optional[str] = None,
    source_output_ids: Sequence[str] = (),
    related_record_ids: Sequence[str] = (),
    score_interpretation: Optional[Mapping[str, Any]] = None,
    policy_contexts: Sequence[Mapping[str, Any]] = (),
    evidence_references: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    if capture_mode not in AI_DECISION_CAPTURE_MODES:
        raise ValueError("unsupported capture_mode")
    source_ids = _unique_identifiers("source_output_ids", source_output_ids)
    related_ids = _unique_identifiers("related_record_ids", related_record_ids)
    policies = [dict(item) for item in policy_contexts]
    policy_keys = [
        (str(item.get("policy_id")), str(item.get("policy_version"))) for item in policies
    ]
    if len(policy_keys) != len(set(policy_keys)):
        raise ValueError("policy_contexts must contain unique policy_id and policy_version pairs")

    actor_value = dict(actor)
    if not actor_value.get("actor_type") or not actor_value.get("actor_id"):
        raise ValueError("actor requires actor_type and actor_id")
    privacy_value = dict(privacy)
    required_privacy = {
        "contains_personal_data",
        "redaction_status",
        "legal_hold_status",
        "public_display_mode",
    }
    if not required_privacy.issubset(privacy_value):
        raise ValueError("privacy is missing required fields")

    result: dict[str, Any] = {
        "decision_type": str(decision_type),
        "declared_outcome": str(declared_outcome),
        "decision_reason": str(decision_reason),
        "source_output_ids": source_ids,
        "decided_at": _timestamp("decided_at", decided_at),
        "policy_contexts": policies,
        "evidence_assessment": dict(evidence_assessment),
        "capture_mode": capture_mode,
        "actor": actor_value,
        "privacy": privacy_value,
        "related_record_ids": related_ids,
        "evidence_references": [dict(item) for item in evidence_references],
    }
    if decision_id is not None:
        result["decision_id"] = _identifier("decision_id", decision_id)
    if operator_id is not None:
        result["operator_id"] = _identifier("operator_id", operator_id)
    if score_interpretation is not None:
        result["score_interpretation"] = dict(score_interpretation)
    canonical_bytes(result)
    return result


class AIDecisionClient:
    """API-key client for first-class AuroraSeal AI Decision v3 records."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        timeout: float = 20.0,
        transport=None,
    ) -> None:
        self.transport = transport or AIDecisionHttpTransport(
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
        value = _json_value(response.body)
        if allow_list:
            if not isinstance(value, list):
                raise AIDecisionTransportError("AURORA returned a non-list JSON response")
            return value
        if not isinstance(value, dict):
            raise AIDecisionTransportError("AURORA returned a non-object JSON response")
        return value

    def list(self, *, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        if not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError("limit must be an integer from 1 to 500")
        if not isinstance(offset, int) or offset < 0:
            raise ValueError("offset must be a non-negative integer")
        query = urlencode({"limit": limit, "offset": offset})
        return self._json_request(
            method="GET",
            endpoint=f"/v1/ai-decisions?{query}",
            allow_list=True,
        )

    def create(
        self,
        request: Mapping[str, Any],
        *,
        idempotency_key: Optional[str] = None,
    ) -> dict[str, Any]:
        canonical_bytes(dict(request))
        return self._json_request(
            method="POST",
            endpoint="/v1/ai-decisions",
            payload=request,
            idempotency_key=idempotency_key or _idempotency_key("create"),
        )

    def get(self, record_id: str) -> dict[str, Any]:
        return self._json_request(
            method="GET",
            endpoint=f"/v1/ai-decisions/{record_id}",
        )

    def seal(
        self,
        record_id: str,
        *,
        idempotency_key: Optional[str] = None,
    ) -> dict[str, Any]:
        return self._json_request(
            method="POST",
            endpoint=f"/v1/ai-decisions/{record_id}/seal",
            idempotency_key=idempotency_key or _idempotency_key("seal"),
        )

    def verify(self, record_id: str) -> dict[str, Any]:
        return self._json_request(
            method="POST",
            endpoint=f"/v1/ai-decisions/{record_id}/verify",
        )

    def download_bundle(self, record_id: str, destination: str | Path) -> Path:
        response = self.transport.request(
            method="GET",
            endpoint=f"/v1/ai-decisions/{record_id}/bundle",
            body=b"",
            idempotency_key=None,
            accept="application/zip",
        )
        if response.status < 200 or response.status >= 300:
            _raise_api_error(response)
        if not response.body.startswith(b"PK"):
            raise AIDecisionTransportError("AURORA returned a non-ZIP bundle")

        target = Path(destination)
        if target.exists() and target.is_dir():
            target = target / f"AuroraSeal_AIDecision_{record_id}.zip"
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
    "AI_DECISION_CAPTURE_MODES",
    "AI_DECISION_EVIDENCE_COMPLETENESS",
    "AI_DECISION_PROFILE_ID",
    "AI_DECISION_PROFILE_VERSION",
    "AI_DECISION_SCHEMA_ID",
    "AI_DECISION_SCHEMA_VERSION",
    "AI_DECISION_SCORE_DIRECTIONS",
    "AI_DECISION_SCORE_SCALE_KINDS",
    "AI_DECISION_SCORE_TRANSFORMS",
    "AIDecisionAPIError",
    "AIDecisionClient",
    "AIDecisionHttpResponse",
    "AIDecisionHttpTransport",
    "AIDecisionTransportError",
    "build_ai_decision_request",
    "build_evidence_assessment",
    "build_evidence_flag",
    "build_policy_context",
    "build_score_interpretation",
    "canonical_decimal",
]