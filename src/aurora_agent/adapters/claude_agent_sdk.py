from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator, Optional

from ..ingestion import RunSession


class ClaudeAgentCaptureAdapter:
    """Framework-light adapter for Claude Agent SDK lifecycle hooks.

    It does not import Anthropic packages. Call these methods from the SDK's
    prompt/model/tool hooks. The adapter preserves JAKROW's explicit approval
    and ``execute_operation`` boundary references in AURORA event metadata.
    """

    def __init__(self, run: RunSession) -> None:
        self.run = run

    def prompt(self, value: Any, **metadata: Any) -> str:
        return self.run.capture("prompt", value, metadata=metadata)

    def retrieved_context(self, value: Any, **metadata: Any) -> str:
        return self.run.capture("retrieved_context", value, metadata=metadata)

    def model_invocation(
        self,
        *,
        model: str,
        provider: str = "anthropic",
        parameters: Optional[dict] = None,
    ) -> str:
        return self.run.capture(
            "model_invocation",
            {"model": model, "provider": provider, "parameters": parameters or {}},
        )

    def model_response(self, value: Any, **metadata: Any) -> str:
        return self.run.capture("model_response", value, metadata=metadata)

    def authorization(
        self,
        *,
        approval_ref: str,
        authorization_digest: str,
        actor: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> str:
        return self.run.capture(
            "authorization",
            {
                "approval_ref": approval_ref,
                "authorization_digest": authorization_digest,
            },
            actor=actor,
            authorization_ref=approval_ref,
            metadata=metadata or {},
        )

    def tool_request(
        self,
        *,
        tool_name: str,
        arguments: Any,
        operation_ref: str,
        approval_ref: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> str:
        return self.run.capture(
            "tool_request",
            {"tool_name": tool_name, "arguments": arguments},
            authorization_ref=approval_ref,
            operation_ref=operation_ref,
            metadata=metadata or {},
        )

    def tool_execution_started(
        self,
        *,
        tool_name: str,
        operation_ref: str,
        request_event_id: str,
        approval_ref: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> str:
        return self.run.capture(
            "tool_execution",
            {
                "tool_name": tool_name,
                "operation_ref": operation_ref,
                "request_event_id": request_event_id,
                "dispatch_state": "ENTERED",
            },
            authorization_ref=approval_ref,
            operation_ref=operation_ref,
            metadata=metadata or {},
        )

    def runtime_failure(
        self,
        *,
        tool_name: str,
        operation_ref: str,
        failure_type: str,
        approval_ref: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> str:
        return self.run.capture(
            "runtime_failure",
            {
                "tool_name": tool_name,
                "operation_ref": operation_ref,
                "dispatch_state": "UNKNOWN",
                "failure_type": failure_type,
                "retry_permitted": False,
            },
            authorization_ref=approval_ref,
            operation_ref=operation_ref,
            metadata=metadata or {},
        )

    @contextmanager
    def execute_operation(
        self,
        *,
        tool_name: str,
        arguments: Any,
        operation_ref: str,
        approval_ref: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> Iterator[None]:
        request_event = self.tool_request(
            tool_name=tool_name,
            arguments=arguments,
            operation_ref=operation_ref,
            approval_ref=approval_ref,
            metadata=metadata,
        )
        self.tool_execution_started(
            tool_name=tool_name,
            operation_ref=operation_ref,
            request_event_id=request_event,
            approval_ref=approval_ref,
        )
        try:
            yield
        except BaseException as exc:
            self.runtime_failure(
                tool_name=tool_name,
                operation_ref=operation_ref,
                failure_type=type(exc).__name__,
                approval_ref=approval_ref,
                metadata={"exception_message_digest_only": True},
            )
            raise

    def tool_outcome(
        self,
        value: Any,
        *,
        operation_ref: str,
        outcome_ref: Optional[str] = None,
        approval_ref: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> str:
        return self.run.capture(
            "tool_outcome",
            value,
            authorization_ref=approval_ref,
            operation_ref=operation_ref,
            outcome_ref=outcome_ref,
            metadata=metadata or {},
        )

    def human_review(self, value: Any, *, actor: Optional[str] = None) -> str:
        return self.run.capture("human_review", value, actor=actor)

    def final_decision(self, value: Any, *, actor: Optional[str] = None) -> str:
        return self.run.capture("final_decision", value, actor=actor)
