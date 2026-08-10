from __future__ import annotations

import json

import pytest

from aurora_agent import (
    HUMAN_APPROVAL_DECLARED_STATES,
    HumanApprovalAPIError,
    HumanApprovalClient,
    build_approval_requirement,
    build_policy_requirement_binding,
)
from aurora_agent.human_approval import HumanApprovalHttpResponse


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, **kwargs):
        self.calls.append(kwargs)
        if not self.responses:
            raise AssertionError("unexpected transport request")
        return self.responses.pop(0)


def _json_response(status: int, payload) -> HumanApprovalHttpResponse:
    return HumanApprovalHttpResponse(
        status=status,
        body=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )


def _requirement() -> dict:
    return build_approval_requirement(
        approval_required=True,
        required_review_level="level_2",
        required_reviewer_roles=["admin", "owner"],
        minimum_approver_count=2,
        separation_of_duties=True,
        escalation_required=False,
    )


def _submission(state: str = "multi_party_approved") -> dict:
    return {
        "approval_process_id": "proc-1",
        "event_sequence": 2,
        "declared_resulting_state": state,
        "requirement_snapshot": _requirement(),
        "policy_source_decision_record_id": "ase-decision-1",
    }


def test_requirement_builder_preserves_frozen_set_order_and_shape():
    assert _requirement() == {
        "approval_required": True,
        "required_review_level": "level_2",
        "required_reviewer_roles": ["admin", "owner"],
        "minimum_approver_count": 2,
        "separation_of_duties": True,
        "escalation_required": False,
    }
    assert "second_reviewer_required" in HUMAN_APPROVAL_DECLARED_STATES
    assert "multi_party_approved" in HUMAN_APPROVAL_DECLARED_STATES

    with pytest.raises(ValueError, match="lexicographically ascending"):
        build_approval_requirement(
            approval_required=True,
            required_review_level="level_2",
            required_reviewer_roles=["owner", "admin"],
            minimum_approver_count=2,
            separation_of_duties=True,
            escalation_required=False,
        )

    with pytest.raises(ValueError, match="duplicates"):
        build_approval_requirement(
            approval_required=True,
            required_review_level="level_2",
            required_reviewer_roles=["owner", "owner"],
            minimum_approver_count=2,
            separation_of_duties=True,
            escalation_required=False,
        )


def test_policy_requirement_builder_rejects_non_v1_snapshot_shape():
    request = build_policy_requirement_binding(
        policy_id="release-gate",
        policy_version="1.0",
        policy_digest="sha256:" + "a" * 64,
        requirement_snapshot=_requirement(),
    )
    assert request["requirement_snapshot"] == _requirement()

    malformed = dict(_requirement())
    malformed["client_override"] = True
    with pytest.raises(ValueError, match="exact v1 fields"):
        build_policy_requirement_binding(
            policy_id="release-gate",
            policy_version="1.0",
            policy_digest="sha256:" + "a" * 64,
            requirement_snapshot=malformed,
        )


def test_read_surfaces_use_v1_gate_review_and_process_endpoints():
    transport = FakeTransport(
        [
            _json_response(200, {"resolution_status": "RESOLVED"}),
            _json_response(200, {"events": []}),
            _json_response(200, {"process_status": "PENDING"}),
        ]
    )
    client = HumanApprovalClient(
        base_url="https://example.test",
        api_key="ak_live_test",
        transport=transport,
    )

    assert client.gate(
        "ase-1",
        approval_process_id="proc-1",
        policy_source_decision_record_id="ase-decision-1",
    )["resolution_status"] == "RESOLVED"
    assert client.list_reviews("ase-1")["events"] == []
    assert client.process(
        "ase-1",
        "proc-1",
        policy_source_decision_record_id="ase-decision-1",
    )["process_status"] == "PENDING"

    assert transport.calls[0]["endpoint"] == (
        "/v1/records/ase-1/approval-gate?approval_process_id=proc-1&"
        "policy_source_decision_record_id=ase-decision-1"
    )
    assert transport.calls[1]["endpoint"] == "/v1/records/ase-1/reviews"
    assert transport.calls[2]["endpoint"] == (
        "/v1/records/ase-1/approval-processes/proc-1?"
        "policy_source_decision_record_id=ase-decision-1"
    )
    assert all(call["reviewer_token"] is None for call in transport.calls)


def test_reviewer_operations_fail_locally_without_reviewer_token():
    transport = FakeTransport([])
    client = HumanApprovalClient(
        base_url="https://example.test",
        api_key="ak_live_test",
        transport=transport,
    )

    with pytest.raises(ValueError, match="reviewer_token is required"):
        client.eligibility("ase-1", "proc-1")
    with pytest.raises(ValueError, match="reviewer_token is required"):
        client.approve("ase-1", _submission())
    assert transport.calls == []


def test_eligibility_and_policy_binding_forward_reviewer_token_only_on_human_boundary():
    transport = FakeTransport(
        [
            _json_response(200, {"eligible_to_count": True}),
            _json_response(201, {"binding_id": "aprpol_1"}),
        ]
    )
    client = HumanApprovalClient(
        base_url="https://example.test",
        api_key="ak_live_test",
        reviewer_token="eyJ.reviewer",
        transport=transport,
    )

    assert client.eligibility("ase-1", "proc-1")["eligible_to_count"] is True
    binding = build_policy_requirement_binding(
        policy_id="release-gate",
        policy_version="1.0",
        policy_digest="sha256:" + "a" * 64,
        requirement_snapshot=_requirement(),
    )
    assert client.register_policy_requirement(
        binding,
        idempotency_key="binding-1",
    )["binding_id"] == "aprpol_1"

    assert [call["reviewer_token"] for call in transport.calls] == [
        "eyJ.reviewer",
        "eyJ.reviewer",
    ]
    assert transport.calls[1]["idempotency_key"] == "binding-1"


def test_approve_from_eligibility_uses_exact_server_submission_state():
    eligibility = {
        "eligible_to_count": True,
        "ineligibility_reasons": [],
        "expected_declared_resulting_state_if_approved": "multi_party_approved",
        "approval_submission": _submission("multi_party_approved"),
    }
    transport = FakeTransport([_json_response(201, {"event": {"event_sequence": 2}})])
    client = HumanApprovalClient(
        base_url="https://example.test",
        api_key="ak_live_test",
        reviewer_token="eyJ.admin",
        transport=transport,
    )

    result = client.approve_from_eligibility(
        "ase-1",
        eligibility,
        reason_code="policy_requirement_met",
        reason="Second distinct approval.",
        idempotency_key="approve-2",
    )
    assert result["event"]["event_sequence"] == 2

    call = transport.calls[0]
    assert call["endpoint"] == "/v1/records/ase-1/approve"
    assert call["reviewer_token"] == "eyJ.admin"
    assert call["idempotency_key"] == "approve-2"
    payload = json.loads(call["body"].decode("utf-8"))
    assert payload["approval_process_id"] == "proc-1"
    assert payload["event_sequence"] == 2
    assert payload["declared_resulting_state"] == "multi_party_approved"
    assert payload["requirement_snapshot"] == _requirement()
    assert payload["policy_source_decision_record_id"] == "ase-decision-1"
    assert payload["policy_acknowledged"] is True
    assert payload["execution_authorization_granted"] is False
    assert "decision_responsibility_accepted" not in payload


def test_all_review_write_methods_preserve_endpoint_and_api_error_envelope():
    transport = FakeTransport(
        [
            _json_response(201, {"ok": "review"}),
            _json_response(201, {"ok": "override"}),
            _json_response(201, {"ok": "escalate"}),
            _json_response(201, {"ok": "defer"}),
            _json_response(
                409,
                {
                    "error": {
                        "code": "HUMAN_REVIEW_CONFLICT",
                        "detail": "sequence conflict",
                        "retry_permitted": False,
                    }
                },
            ),
        ]
    )
    client = HumanApprovalClient(
        base_url="https://example.test",
        api_key="ak_live_test",
        reviewer_token="eyJ.owner",
        transport=transport,
    )
    request = _submission("human_reviewed")

    assert client.review("ase-1", request, idempotency_key="review-1")["ok"] == "review"
    assert client.override("ase-1", request, idempotency_key="override-1")["ok"] == "override"
    assert client.escalate("ase-1", request, idempotency_key="escalate-1")["ok"] == "escalate"
    assert client.defer("ase-1", request, idempotency_key="defer-1")["ok"] == "defer"
    with pytest.raises(HumanApprovalAPIError) as exc_info:
        client.reject("ase-1", request, idempotency_key="reject-1")

    assert [call["endpoint"] for call in transport.calls] == [
        "/v1/records/ase-1/reviews",
        "/v1/records/ase-1/override",
        "/v1/records/ase-1/escalate",
        "/v1/records/ase-1/defer",
        "/v1/records/ase-1/reject",
    ]
    assert exc_info.value.status == 409
    assert exc_info.value.code == "HUMAN_REVIEW_CONFLICT"
    assert exc_info.value.retry_permitted is False