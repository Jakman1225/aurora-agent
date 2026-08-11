from __future__ import annotations

import json
from pathlib import Path

import pytest

from aurora_agent import (
    AMENDMENT_TYPES,
    AmendmentAPIError,
    AmendmentClient,
    build_amendment_request,
    build_amendment_request_from_lifecycle,
)
from aurora_agent.amendments import AmendmentHttpResponse


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, **kwargs):
        self.calls.append(kwargs)
        if not self.responses:
            raise AssertionError("unexpected transport request")
        return self.responses.pop(0)


def response(status, payload, content_type="application/json"):
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
    return AmendmentHttpResponse(
        status=status,
        body=body,
        headers={"Content-Type": content_type},
    )


def lifecycle():
    return {
        "root_record_id": "ase-root",
        "root_record_digest": "sha256:" + "1" * 64,
        "chain_integrity": "VALID",
        "currentness_asserted": True,
        "head": {
            "current_record_id": "ase-current",
            "current_record_digest": "sha256:" + "2" * 64,
            "latest_amendment_record_id": "ase-amendment-1",
            "latest_amendment_digest": "sha256:" + "3" * 64,
            "amendment_count": 1,
        },
        "expected_head": {
            "expected_current_record_id": "ase-current",
            "expected_current_record_digest": "sha256:" + "2" * 64,
            "expected_previous_amendment_digest": "sha256:" + "3" * 64,
        },
        "viewed_record": {
            "record_id": "ase-current",
            "record_digest": "sha256:" + "2" * 64,
            "record_type": "ai_decision",
            "profile_id": "auroraseal.ai_decision",
            "lifecycle_role": "CURRENT",
            "effect_on_record": "active",
            "later_amendment_exists": False,
        },
        "amendments": [],
        "errors": [],
    }


def actor():
    return {
        "actor_type": "human",
        "actor_id": "operator-1",
        "actor_role": "organization_operator",
    }


def privacy():
    return {
        "contains_personal_data": False,
        "redaction_status": "not_applicable",
        "legal_hold_status": "not_applicable",
        "public_display_mode": "metadata_only",
    }


def test_builder_uses_exact_server_head_and_omits_no_longer_applicable_values():
    request = build_amendment_request_from_lifecycle(
        lifecycle(),
        amendment_type="correction",
        successor_record_id="ase-successor",
        reason_code="declared_outcome_corrected",
        reason="The declared outcome was corrected.",
        occurred_at="2026-08-11T13:00:00.000000Z",
        actor=actor(),
        privacy=privacy(),
    )
    assert request["target_record_id"] == "ase-current"
    assert request["expected_current_record_id"] == "ase-current"
    assert request["expected_current_record_digest"] == "sha256:" + "2" * 64
    assert request["expected_previous_amendment_digest"] == "sha256:" + "3" * 64
    assert request["successor_record_id"] == "ase-successor"
    assert "correction" in AMENDMENT_TYPES


def test_builder_rejects_invalid_terminal_and_successor_semantics():
    base = dict(
        target_record_id="ase-current",
        reason_code="operator_withdrawal",
        reason="Withdrawn from operational use.",
        occurred_at="2026-08-11T13:00:00.000000Z",
        actor=actor(),
        privacy=privacy(),
        expected_current_record_id="ase-current",
        expected_current_record_digest="sha256:" + "2" * 64,
    )
    with pytest.raises(ValueError, match="requires successor_record_id"):
        build_amendment_request(amendment_type="correction", **base)
    with pytest.raises(ValueError, match="withdrawal forbids"):
        build_amendment_request(
            amendment_type="withdrawal",
            successor_record_id="ase-successor",
            **base,
        )


def test_builder_rejects_stale_target_mismatch_and_explicit_bad_digest():
    with pytest.raises(ValueError, match="must equal"):
        build_amendment_request(
            amendment_type="reversal",
            target_record_id="ase-old",
            reason_code="operational_reversal",
            reason="Reversed.",
            occurred_at="2026-08-11T13:00:00.000000Z",
            actor=actor(),
            privacy=privacy(),
            expected_current_record_id="ase-current",
            expected_current_record_digest="sha256:" + "2" * 64,
        )
    with pytest.raises(ValueError, match="sha256"):
        build_amendment_request(
            amendment_type="withdrawal",
            target_record_id="ase-current",
            reason_code="operator_withdrawal",
            reason="Withdrawn.",
            occurred_at="2026-08-11T13:00:00.000000Z",
            actor=actor(),
            privacy=privacy(),
            expected_current_record_id="ase-current",
            expected_current_record_digest="not-a-digest",
        )


def test_client_read_surfaces_use_stage_e_routes():
    transport = FakeTransport(
        [
            response(200, lifecycle()),
            response(200, {"record_id": "ase-amendment-1"}),
        ]
    )
    client = AmendmentClient(
        base_url="https://example.test",
        api_key="ak_live_test",
        transport=transport,
    )
    assert client.lifecycle("ase-current")["chain_integrity"] == "VALID"
    assert client.get_amendment("ase-amendment-1")["record_id"] == "ase-amendment-1"
    assert transport.calls[0]["endpoint"] == "/v1/records/ase-current/lifecycle"
    assert transport.calls[1]["endpoint"] == "/v1/amendments/ase-amendment-1"


def test_successor_preparation_preserves_full_request_and_idempotency():
    transport = FakeTransport(
        [
            response(201, {"successor_record_id": "ase-out-next"}),
            response(201, {"successor_record_id": "ase-dec-next"}),
        ]
    )
    client = AmendmentClient(
        base_url="https://example.test",
        api_key="ak_live_test",
        transport=transport,
    )
    output_request = {
        "output_format": "json",
        "capture_mode": "DIGEST_ONLY",
        "input_digest": "sha256:" + "a" * 64,
        "output_digest": "sha256:" + "b" * 64,
        "generated_at": "2026-08-11T13:00:00.000000Z",
        "actor": actor(),
        "privacy": privacy(),
    }
    decision_request = {
        "decision_type": "risk_review",
        "declared_outcome": "manual_review",
        "decision_reason": "Updated decision.",
        "source_output_ids": [],
        "decided_at": "2026-08-11T13:00:00.000000Z",
        "policy_contexts": [],
        "evidence_assessment": {"evidence_completeness": "complete"},
        "capture_mode": "DIGEST_ONLY",
        "actor": actor(),
        "privacy": privacy(),
        "related_record_ids": [],
        "evidence_references": [],
    }

    client.prepare_ai_output_successor(
        "ase-output",
        output_request,
        idempotency_key="successor-output-1",
    )
    client.prepare_ai_decision_successor(
        "ase-decision",
        decision_request,
        idempotency_key="successor-decision-1",
    )

    assert transport.calls[0]["endpoint"] == "/v1/amendments/successors/ai-output"
    assert transport.calls[0]["idempotency_key"] == "successor-output-1"
    assert json.loads(transport.calls[0]["body"])["successor"] == output_request
    assert transport.calls[1]["endpoint"] == "/v1/amendments/successors/ai-decision"
    assert transport.calls[1]["idempotency_key"] == "successor-decision-1"


def test_seal_amendment_forwards_exact_request_and_surfaces_head_conflict():
    request = build_amendment_request_from_lifecycle(
        lifecycle(),
        amendment_type="correction",
        successor_record_id="ase-successor",
        reason_code="declared_outcome_corrected",
        reason="Corrected.",
        occurred_at="2026-08-11T13:00:00.000000Z",
        actor=actor(),
        privacy=privacy(),
    )
    transport = FakeTransport(
        [
            response(
                201,
                {
                    "amendment": {"record_id": "ase-amendment-2"},
                    "lifecycle": {"chain_integrity": "VALID"},
                },
            ),
            response(
                409,
                {
                    "error": {
                        "code": "AMENDMENT_CHAIN_HEAD_CONFLICT",
                        "detail": "expected head is stale",
                        "retry_permitted": False,
                    }
                },
            ),
        ]
    )
    client = AmendmentClient(
        base_url="https://example.test",
        api_key="ak_live_test",
        transport=transport,
    )
    result = client.seal_amendment(request, idempotency_key="seal-amendment-2")
    assert result["amendment"]["record_id"] == "ase-amendment-2"
    assert json.loads(transport.calls[0]["body"]) == request

    with pytest.raises(AmendmentAPIError) as exc_info:
        client.seal_amendment(request, idempotency_key="different-attempt")
    assert exc_info.value.status == 409
    assert exc_info.value.code == "AMENDMENT_CHAIN_HEAD_CONFLICT"


def test_download_lifecycle_bundle_writes_atomically(tmp_path: Path):
    transport = FakeTransport(
        [response(200, b"PK\x03\x04example", content_type="application/zip")]
    )
    client = AmendmentClient(
        base_url="https://example.test",
        api_key="ak_live_test",
        transport=transport,
    )
    path = client.download_lifecycle_bundle("ase-current", tmp_path)
    assert path.name == "AuroraSeal_Lifecycle_ase-current.zip"
    assert path.read_bytes().startswith(b"PK")
    assert transport.calls[0]["endpoint"] == "/v1/records/ase-current/lifecycle-bundle"
    assert transport.calls[0]["accept"] == "application/zip"