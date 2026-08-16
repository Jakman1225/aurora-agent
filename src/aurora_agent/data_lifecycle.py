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
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .canonical import canonical_bytes
from .exceptions import AuroraAgentError

DATA_LIFECYCLE_PROFILE_ID = "auroraseal.data_lifecycle"
DATA_LIFECYCLE_PROFILE_VERSION = "1.0"
DATA_LIFECYCLE_RECORD_TYPES = ("ai_output", "ai_decision")

_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")


class DataLifecycleTransportError(AuroraAgentError):
    """Network or protocol failure before a valid AURORA response exists."""


class DataLifecycleAPIError(AuroraAgentError):
    def __init__(self, *, status: int, code: str, detail: str, retry_permitted: bool = False) -> None:
        super().__init__(f"HTTP {status} {code}: {detail}")
        self.status = int(status)
        self.code = str(code)
        self.detail = str(detail)
        self.retry_permitted = bool(retry_permitted)


@dataclass(frozen=True)
class DataLifecycleHttpResponse:
    status: int
    body: bytes
    headers: Mapping[str, str]


class DataLifecycleHttpTransport:
    def __init__(self, *, base_url: str, api_key: str, timeout: float = 20.0) -> None:
        if not isinstance(base_url, str) or not base_url.startswith(("https://", "http://")):
            raise ValueError("base_url must be an HTTP(S) URL")
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError("api_key must not be empty")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = float(timeout)

    def request(self, *, method: str, endpoint: str, body: bytes = b"", idempotency_key: Optional[str] = None, accept: str = "application/json") -> DataLifecycleHttpResponse:
        headers = {"Authorization": f"Bearer {self.api_key}", "Accept": accept}
        if body:
            headers["Content-Type"] = "application/json"
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        request = Request(self.base_url + endpoint, data=body if method not in {"GET", "HEAD"} and body else None, method=method, headers=headers)
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return DataLifecycleHttpResponse(int(response.status), response.read(), dict(response.headers.items()))
        except HTTPError as exc:
            return DataLifecycleHttpResponse(int(exc.code), exc.read(), dict(exc.headers.items()))
        except (URLError, OSError) as exc:
            raise DataLifecycleTransportError(str(getattr(exc, "reason", exc))) from exc


def _identifier(name: str, value: str) -> str:
    text = str(value or "")
    if _IDENTIFIER_RE.fullmatch(text) is None:
        raise ValueError(f"{name} must be a valid AuroraSeal identifier")
    return text


def _digest(value: str) -> str:
    text = str(value or "")
    if _DIGEST_RE.fullmatch(text) is None:
        raise ValueError("record_digest must match sha256:<64 lowercase hex>")
    return text


def _record_type(value: str) -> str:
    if value not in DATA_LIFECYCLE_RECORD_TYPES:
        raise ValueError(f"record_type must be one of {DATA_LIFECYCLE_RECORD_TYPES}")
    return value


def _json(raw: bytes) -> Any:
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DataLifecycleTransportError("AURORA returned invalid JSON") from exc


def _raise_error(response: DataLifecycleHttpResponse) -> None:
    try:
        payload = _json(response.body)
    except DataLifecycleTransportError:
        payload = None
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        raise DataLifecycleAPIError(status=response.status, code=str(error.get("code") or "HTTP_ERROR"), detail=str(error.get("detail") or "AURORA request failed"), retry_permitted=bool(error.get("retry_permitted")))
    raise DataLifecycleAPIError(status=response.status, code="HTTP_ERROR", detail="AURORA request failed")


def _key(operation: str) -> str:
    return f"auroraseal.data-lifecycle.{operation}:{uuid.uuid4().hex}"


class DataLifecycleClient:
    """API-key client for the frozen Stage F v1 control plane."""

    def __init__(self, *, base_url: str, api_key: str, timeout: float = 20.0, transport=None) -> None:
        self.transport = transport or DataLifecycleHttpTransport(base_url=base_url, api_key=api_key, timeout=timeout)

    def _request(self, *, method: str, endpoint: str, payload: Optional[Mapping[str, Any]] = None, idempotency_key: Optional[str] = None) -> dict[str, Any]:
        body = canonical_bytes(dict(payload)) if payload is not None else b""
        response = self.transport.request(method=method, endpoint=endpoint, body=body, idempotency_key=idempotency_key, accept="application/json")
        if not 200 <= response.status < 300:
            _raise_error(response)
        value = _json(response.body)
        if not isinstance(value, dict):
            raise DataLifecycleTransportError("AURORA returned a non-object JSON response")
        return value

    def seal_object(self, payload: Mapping[str, Any], *, idempotency_key: Optional[str] = None) -> dict[str, Any]:
        return self._request(method="POST", endpoint="/v1/data-lifecycle/objects", payload=payload, idempotency_key=idempotency_key or _key("object"))

    def register_artifact(self, payload: Mapping[str, Any], *, idempotency_key: Optional[str] = None) -> dict[str, Any]:
        return self._request(method="POST", endpoint="/v1/data-lifecycle/artifacts", payload=payload, idempotency_key=idempotency_key or _key("artifact"))

    def register_content_artifact(self, payload: Mapping[str, Any], *, idempotency_key: Optional[str] = None) -> dict[str, Any]:
        return self._request(method="POST", endpoint="/v1/data-lifecycle/content-artifacts", payload=payload, idempotency_key=idempotency_key or _key("content-artifact"))

    def prepare_operation_intent(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self._request(method="POST", endpoint="/v1/data-lifecycle/operation-intents/prepare", payload=payload)

    def append_event(self, payload: Mapping[str, Any], *, idempotency_key: Optional[str] = None) -> dict[str, Any]:
        return self._request(method="POST", endpoint="/v1/data-lifecycle/events", payload=payload, idempotency_key=idempotency_key or _key("event"))

    def projection(self, *, record_id: str, record_digest: str, record_type: str) -> dict[str, Any]:
        query = urlencode({"record_digest": _digest(record_digest), "record_type": _record_type(record_type)})
        return self._request(method="GET", endpoint=f"/v1/data-lifecycle/records/{_identifier('record_id', record_id)}?{query}")

    def download_verification_bundle(self, *, record_id: str, record_digest: str, record_type: str, destination: str | Path) -> Path:
        record_id = _identifier("record_id", record_id)
        query = urlencode({"record_digest": _digest(record_digest), "record_type": _record_type(record_type)})
        response = self.transport.request(method="GET", endpoint=f"/v1/data-lifecycle/records/{record_id}/verification-bundle?{query}", accept="application/zip")
        if not 200 <= response.status < 300:
            _raise_error(response)
        if not response.body.startswith(b"PK"):
            raise DataLifecycleTransportError("AURORA returned a non-ZIP data lifecycle bundle")
        target = Path(destination)
        if target.exists() and target.is_dir():
            target = target / f"AuroraSeal_Data_Lifecycle_{record_id}.zip"
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


__all__ = ["DATA_LIFECYCLE_PROFILE_ID", "DATA_LIFECYCLE_PROFILE_VERSION", "DATA_LIFECYCLE_RECORD_TYPES", "DataLifecycleAPIError", "DataLifecycleClient", "DataLifecycleHttpResponse", "DataLifecycleHttpTransport", "DataLifecycleTransportError"]