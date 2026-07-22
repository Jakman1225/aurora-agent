"""JAKROW D3 resumed-execution observer for AURORA ingestion.

The observer is local-first.  Every hook writes deterministic events to the
SQLite outbox.  Consequential execution is therefore separated from network
availability, while repeated recovery calls converge on the same event IDs and
one finalization request.
"""

from __future__ import annotations

import hashlib
from typing import Any, Optional

from ..canonical import canonical_bytes
from ..ingestion import RunSession


def _to_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if hasattr(value, "to_dict"):
        result = value.to_dict()
        if isinstance(result, dict):
            return result
    if isinstance(value, dict):
        return dict(value)
    return {"value": str(value)}


def _stable_event_id(run_id: str, approval_ref: str, phase: str) -> str:
    material = canonical_bytes(
        {"run_id": run_id, "approval_ref": approval_ref, "phase": phase}
    )
    return "evt_jakrow_" + hashlib.sha256(material).hexdigest()[:32]


class JAKROWD3IngestionObserver:
    """Project one JAKROW D3 execution into an AURORA ingestion run.

    Event IDs are deterministic for the tuple ``(run_id, approval_ref, phase)``.
    A process restart may therefore complete missing terminal evidence without
    adding duplicate nodes.  By default the observer queues a ``final_decision``
    event and finalization after every durable terminal, deterministic
    pre-consequence abort, or honest outcome-unknown recovery.
    """

    def __init__(self, run: RunSession, *, auto_finalize: bool = True) -> None:
        self.run = run
        self.auto_finalize = bool(auto_finalize)
        self.last_event_id: str | None = None
        self.operation_ref: str | None = None

    def _id(self, approval_ref: str, phase: str) -> str:
        return _stable_event_id(self.run.run_id, approval_ref, phase)

    def _capture_authorization_and_request(
        self,
        *,
        request: Any,
        approval_ref: str,
        authorized_commitment: Any,
        started_at: str,
        dispatched_payload_digest: str,
    ) -> tuple[str, str]:
        commitment_data = _to_dict(authorized_commitment)
        authorization_digest = str(
            commitment_data.get("authorization_digest")
            or commitment_data.get("commitment_digest")
            or commitment_data.get("commitment_id")
        )
        authorization_event = self.run.capture(
            "authorization",
            {
                "approval_ref": approval_ref,
                "authorization_digest": authorization_digest,
            },
            event_id=self._id(approval_ref, "authorization"),
            parent_event_ids=[],
            authorization_ref=approval_ref,
            metadata={
                "jakrow_authorized_commitment_id": commitment_data.get(
                    "commitment_id"
                ),
                "jakrow_started_at": started_at,
                "approved_payload_digest": commitment_data.get(
                    "proposal_payload_digest"
                ),
            },
        )
        correlation_ref = f"action:{request.action_id}"
        request_event = self.run.capture(
            "tool_request",
            {"tool_name": request.tool_name, "arguments": request.arguments},
            event_id=self._id(approval_ref, "tool_request"),
            parent_event_ids=[authorization_event],
            authorization_ref=approval_ref,
            operation_ref=correlation_ref,
            metadata={
                "jakrow_run_id": request.run_id,
                "jakrow_action_id": request.action_id,
                "dispatched_payload_digest": dispatched_payload_digest,
            },
        )
        self.operation_ref = correlation_ref
        return authorization_event, request_event

    def _queue_final_decision(
        self,
        *,
        request: Any,
        approval_ref: str,
        parent_event_id: str,
        decision_state: str,
        terminal_digest: Optional[str] = None,
        failure_code: Optional[str] = None,
    ) -> tuple[str, Optional[str]]:
        final_event = self.run.capture(
            "final_decision",
            {
                "decision_state": decision_state,
                "tool_name": request.tool_name,
                "retry_permitted": False,
                "terminal_digest": terminal_digest,
                "failure_code": failure_code,
            },
            event_id=self._id(approval_ref, "final_decision"),
            parent_event_ids=[parent_event_id],
            authorization_ref=approval_ref,
            operation_ref=self.operation_ref,
            metadata={
                "jakrow_run_id": request.run_id,
                "jakrow_action_id": request.action_id,
                "automatic_consequential_retry": False,
            },
        )
        finalize_key: Optional[str] = None
        if self.auto_finalize:
            item = self.run.finalize(root_event_id=final_event)
            finalize_key = item.request_key
        self.last_event_id = final_event
        return final_event, finalize_key

    def before_consequence(
        self,
        *,
        request: Any,
        approval_ref: str,
        authorized_commitment: Any,
        started_at: str,
        dispatched_payload_digest: str,
    ) -> dict[str, Any]:
        authorization_event, request_event = self._capture_authorization_and_request(
            request=request,
            approval_ref=approval_ref,
            authorized_commitment=authorized_commitment,
            started_at=started_at,
            dispatched_payload_digest=dispatched_payload_digest,
        )
        correlation_ref = f"action:{request.action_id}"
        execution_event = self.run.capture(
            "tool_execution",
            {
                "tool_name": request.tool_name,
                "operation_ref": correlation_ref,
                "request_event_id": request_event,
                "dispatch_state": "ENTERED",
            },
            event_id=self._id(approval_ref, "tool_execution"),
            parent_event_ids=[request_event],
            authorization_ref=approval_ref,
            operation_ref=correlation_ref,
            metadata={"commit_before_consequence": True},
        )
        self.operation_ref = correlation_ref
        self.last_event_id = execution_event
        return {
            "status": "QUEUED_LOCALLY",
            "authorization_event_id": authorization_event,
            "request_event_id": request_event,
            "execution_event_id": execution_event,
        }

    def terminal(
        self,
        *,
        request: Any,
        approval_ref: str,
        operation: Any,
        terminal: Any,
        verification: Any,
        authorized_commitment: Any = None,
        started_at: Optional[str] = None,
        dispatched_payload_digest: Optional[str] = None,
    ) -> dict[str, Any]:
        operation_data = _to_dict(operation)
        terminal_data = _to_dict(terminal)
        verification_data = _to_dict(verification)
        operation_ref = str(
            operation_data.get("operation_id")
            or terminal_data.get("operation_reference")
            or self.operation_ref
            or f"action:{request.action_id}"
        )

        execution_event = self._id(approval_ref, "tool_execution")
        execution_key = f"event:{self.run.run_id}:{execution_event}"
        if self.run.client.outbox.get_item(execution_key) is None:
            if (
                authorized_commitment is None
                or not started_at
                or not dispatched_payload_digest
            ):
                raise RuntimeError(
                    "terminal evidence recovery requires durable STARTED context "
                    "when pre-consequence events are absent"
                )
            _, request_event = self._capture_authorization_and_request(
                request=request,
                approval_ref=approval_ref,
                authorized_commitment=authorized_commitment,
                started_at=started_at,
                dispatched_payload_digest=dispatched_payload_digest,
            )
            execution_event = self.run.capture(
                "tool_execution",
                {
                    "tool_name": request.tool_name,
                    "operation_ref": f"action:{request.action_id}",
                    "request_event_id": request_event,
                    "dispatch_state": "RECOVERED_FROM_DURABLE_TERMINAL",
                },
                event_id=execution_event,
                parent_event_ids=[request_event],
                authorization_ref=approval_ref,
                operation_ref=f"action:{request.action_id}",
                metadata={
                    "commit_before_consequence": True,
                    "reconstructed_from_durable_terminal": True,
                },
            )

        self.operation_ref = operation_ref
        terminal_state = str(terminal_data.get("terminal_state") or "UNKNOWN")
        outcome_event = self.run.capture(
            "tool_outcome",
            {
                "terminal_state": terminal_state,
                "operation": operation_data,
                "terminal_digest": terminal_data.get("terminal_digest"),
                "verification_verdict": verification_data.get("verdict"),
                "outcome_strength": terminal_data.get("outcome_strength", "O0"),
                "provider_acknowledgement": terminal_data.get(
                    "provider_acknowledgement", "NOT_PRESENT"
                ),
                "failure_code": terminal_data.get("failure_code"),
            },
            event_id=self._id(approval_ref, "tool_outcome"),
            parent_event_ids=[execution_event],
            authorization_ref=approval_ref,
            operation_ref=operation_ref,
            outcome_ref=terminal_data.get("terminal_digest"),
            metadata={
                "jakrow_terminal_persisted": True,
                "jakrow_continuity_accepted": bool(
                    getattr(verification, "accepted", False)
                ),
            },
        )
        final_event, finalize_key = self._queue_final_decision(
            request=request,
            approval_ref=approval_ref,
            parent_event_id=outcome_event,
            decision_state=terminal_state,
            terminal_digest=terminal_data.get("terminal_digest"),
            failure_code=terminal_data.get("failure_code"),
        )
        return {
            "status": "QUEUED_LOCALLY",
            "terminal_event_id": outcome_event,
            "final_decision_event_id": final_event,
            "finalize_request_key": finalize_key,
        }

    def precondition_failed(
        self,
        *,
        request: Any,
        approval_ref: str,
        authorized_commitment: Any,
        started_at: str,
        dispatched_payload_digest: str,
        failure_code: str,
        verification: Any = None,
    ) -> dict[str, Any]:
        _, request_event = self._capture_authorization_and_request(
            request=request,
            approval_ref=approval_ref,
            authorized_commitment=authorized_commitment,
            started_at=started_at,
            dispatched_payload_digest=dispatched_payload_digest,
        )
        execution_event = self._id(approval_ref, "tool_execution")
        execution_key = f"event:{self.run.run_id}:{execution_event}"
        parent_event = (
            execution_event
            if self.run.client.outbox.get_item(execution_key) is not None
            else request_event
        )
        dispatch_event_present = parent_event == execution_event
        failure_event = self.run.capture(
            "tool_outcome",
            {
                "terminal_state": "FAILED",
                "decision_state": "FAILED_BEFORE_CONSEQUENCE",
                "tool_name": request.tool_name,
                "operation_ref": f"action:{request.action_id}",
                "dispatch_state": (
                    "ENTERED_NO_CONSEQUENCE"
                    if dispatch_event_present
                    else "NOT_ENTERED"
                ),
                "failure_code": failure_code,
                "retry_permitted": False,
                "consequence_occurred": False,
            },
            event_id=self._id(approval_ref, "precondition_failure"),
            parent_event_ids=[parent_event],
            authorization_ref=approval_ref,
            operation_ref=f"action:{request.action_id}",
            outcome_ref=failure_code,
            metadata={
                "jakrow_run_id": request.run_id,
                "jakrow_action_id": request.action_id,
                "consequence_invoked": False,
                "dispatch_event_present": dispatch_event_present,
                "known_preconsequence_failure": True,
                "verification_verdict": _to_dict(verification).get("verdict"),
            },
        )
        self.operation_ref = f"action:{request.action_id}"
        final_event, finalize_key = self._queue_final_decision(
            request=request,
            approval_ref=approval_ref,
            parent_event_id=failure_event,
            decision_state="FAILED_BEFORE_CONSEQUENCE",
            failure_code=failure_code,
        )
        return {
            "status": "QUEUED_LOCALLY",
            "failure_event_id": failure_event,
            "final_decision_event_id": final_event,
            "finalize_request_key": finalize_key,
        }

    def outcome_unknown(
        self,
        *,
        request: Any,
        approval_ref: str,
        verification: Any,
        authorized_commitment: Any = None,
        started_at: Optional[str] = None,
        dispatched_payload_digest: Optional[str] = None,
    ) -> dict[str, Any]:
        verification_data = _to_dict(verification)
        parent: list[str] = []
        execution_key = f"event:{self.run.run_id}:{self._id(approval_ref, 'tool_execution')}"
        existing_execution = self.run.client.outbox.get_item(execution_key)
        if existing_execution is not None:
            parent = [self._id(approval_ref, "tool_execution")]
        elif authorized_commitment is not None and started_at and dispatched_payload_digest:
            _, request_event = self._capture_authorization_and_request(
                request=request,
                approval_ref=approval_ref,
                authorized_commitment=authorized_commitment,
                started_at=started_at,
                dispatched_payload_digest=dispatched_payload_digest,
            )
            parent = [request_event]

        operation_ref = f"action:{request.action_id}"
        failure_event = self.run.capture(
            "runtime_failure",
            {
                "tool_name": request.tool_name,
                "operation_ref": operation_ref,
                "dispatch_state": "UNKNOWN",
                "failure_type": "OUTCOME_UNKNOWN_AFTER_DURABLE_STARTED",
                "retry_permitted": False,
            },
            event_id=self._id(approval_ref, "outcome_unknown"),
            parent_event_ids=parent,
            authorization_ref=approval_ref,
            operation_ref=operation_ref,
            metadata={
                "jakrow_run_id": request.run_id,
                "jakrow_action_id": request.action_id,
                "retry_permitted": False,
                "verification_verdict": verification_data.get("verdict"),
            },
        )
        self.operation_ref = operation_ref
        final_event, finalize_key = self._queue_final_decision(
            request=request,
            approval_ref=approval_ref,
            parent_event_id=failure_event,
            decision_state="OUTCOME_UNKNOWN",
            failure_code="OUTCOME_UNKNOWN_AFTER_DURABLE_STARTED",
        )
        return {
            "status": "QUEUED_LOCALLY",
            "unknown_event_id": failure_event,
            "final_decision_event_id": final_event,
            "finalize_request_key": finalize_key,
        }


__all__ = ["JAKROWD3IngestionObserver"]
