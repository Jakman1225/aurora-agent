from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from .canonical import canonical_bytes, commitment
from .exceptions import LifecycleError
from .ingestion_constants import (
    CAPTURE_DIGEST_ONLY,
    CAPTURE_FULL_PAYLOAD,
    CAPTURE_MODES,
    CAPTURE_REDACTED,
    EVENT_SCHEMA_ID,
    EVENT_SCHEMA_VERSION,
    EVENT_TYPES,
    FINALIZE_SCHEMA_ID,
    FINALIZE_SCHEMA_VERSION,
    RETRYABLE_STATUS,
    RUN_SCHEMA_ID,
    RUN_SCHEMA_VERSION,
    STATE_ACKNOWLEDGED,
    STATE_CONFLICT,
    STATE_PENDING,
    STATE_REJECTED,
)
from .ingestion_http import IngestionTransportError, UrllibTransport
from .ingestion_outbox import IngestionOutbox, LocalRun, OutboxItem


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _identifier(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"



def _idempotency_key(kind: str, *parts: str) -> str:
    material = canonical_bytes({"kind": kind, "parts": list(parts)})
    return f"jakrow.ingest.{kind}:" + hashlib.sha256(material).hexdigest()


def _json_object(raw: bytes) -> dict[str, Any]:
    value = json.loads(raw.decode("utf-8"))
    return value if isinstance(value, dict) else {"value": value}


class IngestionClient:
    """Local-first client for AURORA incremental evidence ingestion.

    API keys are held only in the transport object and are never persisted in
    the SQLite outbox. DIGEST_ONLY and REDACTED requests queue local commitments,
    not the raw source payload.
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        outbox_path: str | Path = "aurora_ingestion_outbox.db",
        transport=None,
        clock: Callable[[], str] = _utc_now,
        id_factory: Callable[[str], str] = _identifier,
    ) -> None:
        self.outbox = IngestionOutbox(outbox_path)
        self.transport = transport or UrllibTransport(
            base_url=base_url, api_key=api_key
        )
        self.clock = clock
        self.id_factory = id_factory

    def start_run(
        self,
        *,
        atp_id: Optional[str] = None,
        audit_record_id: Optional[str] = None,
        capture_mode: str = CAPTURE_DIGEST_ONLY,
        runtime: str = "JAKROW",
        release_id: str = "jakrow-ingestion-v0.1",
        boundary_id: str = "jakrow.claude-agent-sdk.execute_operation",
        boundary_version: str = "0.1",
        source: str = "JAKROW",
        run_id: Optional[str] = None,
    ) -> "RunSession":
        if (atp_id is None) == (audit_record_id is None):
            raise ValueError("exactly one of atp_id or audit_record_id is required")
        if capture_mode not in CAPTURE_MODES:
            raise ValueError(f"unsupported capture_mode: {capture_mode}")
        resolved_run_id = run_id or self.id_factory("run")
        body: dict[str, Any] = {
            "schema_id": RUN_SCHEMA_ID,
            "schema_version": RUN_SCHEMA_VERSION,
            "run_id": resolved_run_id,
            "capture_mode": capture_mode,
            "runtime": runtime,
            "release_id": release_id,
            "boundary_id": boundary_id,
            "boundary_version": boundary_version,
            "source": source,
        }
        if atp_id is not None:
            body["atp_id"] = atp_id
        else:
            body["audit_record_id"] = audit_record_id
        self.outbox.create_run(
            run_id=resolved_run_id,
            capture_mode=capture_mode,
            request_key=f"run:{resolved_run_id}",
            endpoint="/v1/evidence/runs",
            idempotency_key=_idempotency_key("run", resolved_run_id),
            body=body,
        )
        return RunSession(self, resolved_run_id)

    def resume_run(self, run_id: str) -> "RunSession":
        self.outbox.run(run_id)
        return RunSession(self, run_id)

    def flush(self, *, limit: Optional[int] = None) -> list[OutboxItem]:
        self.outbox.recover_submitting()
        completed: list[OutboxItem] = []
        while limit is None or len(completed) < limit:
            item = self.outbox.claim_next()
            if item is None:
                break
            try:
                response = self.transport.request(
                    method=item.method,
                    endpoint=item.endpoint,
                    body=item.request_bytes,
                    idempotency_key=item.idempotency_key,
                )
            except IngestionTransportError as exc:
                completed.append(
                    self.outbox.complete(
                        item.id,
                        state=STATE_PENDING,
                        status=None,
                        response_bytes=None,
                        error_code=f"TRANSPORT:{exc}",
                    )
                )
                break
            if response.status in {200, 201}:
                state = STATE_ACKNOWLEDGED
                error = None
            elif response.status == 409:
                state = STATE_CONFLICT
                error = "HTTP_409_CONFLICT"
            elif response.status in RETRYABLE_STATUS:
                state = STATE_PENDING
                error = f"HTTP_{response.status}_RETRYABLE"
            else:
                state = STATE_REJECTED
                error = f"HTTP_{response.status}_REJECTED"
            completed.append(
                self.outbox.complete(
                    item.id,
                    state=state,
                    status=response.status,
                    response_bytes=response.body,
                    error_code=error,
                )
            )
            if state != STATE_ACKNOWLEDGED:
                break
        return completed

    def replay_request(self, request_key: str) -> OutboxItem:
        """Requeue one exact acknowledged evidence request for server replay."""

        return self.outbox.requeue_exact(request_key)

    def _request_json(self, *, method: str, endpoint: str) -> dict[str, Any]:
        try:
            response = self.transport.request(
                method=method,
                endpoint=endpoint,
                body=b"",
                idempotency_key=None,
            )
        except IngestionTransportError:
            raise
        if response.status < 200 or response.status >= 300:
            detail = response.body.decode("utf-8", errors="replace")[:500]
            raise IngestionTransportError(
                f"{method} {endpoint} returned HTTP {response.status}: {detail}"
            )
        return _json_object(response.body)

    def read_run(self, run_id: str) -> dict[str, Any]:
        return self._request_json(
            method="GET", endpoint=f"/v1/evidence/runs/{run_id}"
        )

    def verify_run(self, run_id: str) -> dict[str, Any]:
        return self._request_json(
            method="POST", endpoint=f"/v1/evidence/runs/{run_id}/verify"
        )


class RunSession:
    def __init__(self, client: IngestionClient, run_id: str) -> None:
        self.client = client
        self.run_id = run_id

    @property
    def local(self) -> LocalRun:
        return self.client.outbox.run(self.run_id)

    def capture(
        self,
        event_type: str,
        payload: Any,
        *,
        redacted_payload: Any = None,
        redacted_payload_present: bool = False,
        parent_event_ids: Optional[Iterable[str]] = None,
        event_id: Optional[str] = None,
        source: str = "JAKROW",
        actor: Optional[str] = None,
        authorization_ref: Optional[str] = None,
        operation_ref: Optional[str] = None,
        outcome_ref: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
        signature: Optional[dict[str, Any]] = None,
    ) -> str:
        if event_type not in EVENT_TYPES:
            raise ValueError(f"unsupported event_type: {event_type}")
        local = self.local
        resolved_event_id = event_id or self.client.id_factory("evt")
        parents = (
            list(parent_event_ids)
            if parent_event_ids is not None
            else ([local.last_event_id] if local.last_event_id else [])
        )
        if local.capture_mode == CAPTURE_FULL_PAYLOAD:
            payload_input = {"kind": "RAW", "value": payload}
        else:
            payload_input = {"kind": "DIGEST", "value": commitment(payload)}

        request_key = f"event:{self.run_id}:{resolved_event_id}"
        existing = self.client.outbox.get_item(request_key)
        if existing is not None:
            stored = _json_object(existing.request_bytes)
            expected = {
                "event_id": resolved_event_id,
                "event_type": event_type,
                "source": source,
                "metadata": metadata or {},
                "payload_input": payload_input,
                "parent_event_ids": parents,
                "actor": actor,
                "authorization_ref": authorization_ref,
                "operation_ref": operation_ref,
                "outcome_ref": outcome_ref,
                "signature": signature,
            }
            if local.capture_mode == CAPTURE_REDACTED:
                if not redacted_payload_present:
                    raise ValueError(
                        "REDACTED capture requires redacted_payload_present=True"
                    )
                expected["redacted_payload"] = redacted_payload
            mismatched = [
                key for key, value in expected.items() if stored.get(key) != value
            ]
            if mismatched:
                raise LifecycleError(
                    "event_id is already bound to different local evidence fields: "
                    + ", ".join(sorted(mismatched))
                )
            return resolved_event_id

        body: dict[str, Any] = {
            "schema_id": EVENT_SCHEMA_ID,
            "schema_version": EVENT_SCHEMA_VERSION,
            "event_id": resolved_event_id,
            "sequence": local.next_sequence,
            "event_type": event_type,
            "captured_at": self.client.clock(),
            "parent_event_ids": parents,
            "source": source,
            "metadata": metadata or {},
            "payload_input": payload_input,
        }
        for key, value in (
            ("actor", actor),
            ("authorization_ref", authorization_ref),
            ("operation_ref", operation_ref),
            ("outcome_ref", outcome_ref),
            ("signature", signature),
        ):
            if value is not None:
                body[key] = value
        if local.capture_mode == CAPTURE_REDACTED:
            if not redacted_payload_present:
                raise ValueError(
                    "REDACTED capture requires redacted_payload_present=True"
                )
            body["redacted_payload"] = redacted_payload
        self.client.outbox.append_event(
            run_id=self.run_id,
            event_id=resolved_event_id,
            request_key=request_key,
            endpoint=f"/v1/evidence/runs/{self.run_id}/events",
            idempotency_key=_idempotency_key("event", self.run_id, resolved_event_id),
            body=body,
        )
        return resolved_event_id

    def finalize(
        self,
        *,
        root_event_id: Optional[str] = None,
        graph_id: Optional[str] = None,
    ) -> OutboxItem:
        local = self.local
        root = root_event_id or local.last_event_id
        if not root:
            raise LifecycleError("cannot finalize a run without events")
        body: dict[str, Any] = {
            "schema_id": FINALIZE_SCHEMA_ID,
            "schema_version": FINALIZE_SCHEMA_VERSION,
            "root_event_id": root,
        }
        if graph_id is not None:
            body["graph_id"] = graph_id
        return self.client.outbox.queue_finalize(
            run_id=self.run_id,
            request_key=f"finalize:{self.run_id}",
            endpoint=f"/v1/evidence/runs/{self.run_id}/finalize",
            idempotency_key=_idempotency_key("finalize", self.run_id),
            body=body,
        )

    def requests(self) -> list[OutboxItem]:
        return self.client.outbox.items(self.run_id)
