from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from aurora_agent import (
    AIDecisionAPIError,
    AIDecisionClient,
    AIDecisionTransportError,
    build_ai_decision_request,
    build_evidence_assessment,
    build_evidence_flag,
    build_policy_context,
    build_score_interpretation,
    canonical_decimal,
)
from aurora_agent.ai_decisions import AIDecisionHttpResponse


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, **kwargs):
        self.calls.append(kwargs)
        if not self.responses:
            raise AssertionError("unexpected transport request")
        return self.responses.pop(0)


def _json_response(status: int, payload) -> AIDecisionHttpResponse:
    return AIDecisionHttpResponse(
        status=status,
        body=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )


def _record(record_id: str = "asd_test") -> dict:
    return {
        "record": {
            "subject": {
                "record_id": record_id,
                "record_type": "ai_decision",
                "payload": {
                    "decision_id": "decision_test",
                    "decision_type": "risk_review",
                    "declared_outcome": "manual_review",
                },
            },
            "seal": {"seal_state": "DIGESTED"},
        },
        "decision_id": "decision_test",
    }


def _request() -> dict:
    score = build_score_interpretation(
        score_value="0.82",
        scale_kind="bounded_numeric",
        transform="probability",
        minimum="0",
        maximum="1",
        score_direction="higher_is_riskier",
        score_source="risk-model-v4",
        threshold_value="0.75",
        threshold_meaning="manual review threshold",
        risk_band="high",
    )
    policy = build_policy_context(
        policy_id="credit-risk",
        policy_name="Credit Risk Review",
        policy_version="4.2",
        policy_digest="sha256:" + "a" * 64,
        policy_sections=["4.1", "4.2"],
        effective_at="2026-07-29T12:00:00.000000Z",
    )
    assessment = build_evidence_assessment(
        evidence_completeness="partial",
        uncertainty_flags=[
            build_evidence_flag(
                code="MODEL_DRIFT_UNCHECKED",
                description="The current drift report was not available.",
                evidence_ids=["drift-report-request"],
            )
        ],
    )
    return build_ai_decision_request(
        decision_id="decision_test",
        decision_type="risk_review",
        declared_outcome="manual_review",
        decision_reason="The declared score exceeded the review threshold.",
        decided_at="2026-07-29T12:01:00.000000Z",
        source_output_ids=["ase_source_1"],
        operator_id="operator_1",
        score_interpretation=score,
        policy_contexts=[policy],
        evidence_assessment=assessment,
        capture_mode="DIGEST_ONLY",
        actor={
            "actor_type": "service",
            "actor_id": "decision-service",
            "actor_role": "policy-evaluator",
        },
        privacy={
            "contains_personal_data": False,
            "redaction_status": "not_applicable",
            "legal_hold_status": "not_applicable",
            "public_display_mode": "metadata_only",
        },
    )


def test_canonical_decimal_rejects_floats_and_exponents():
    assert canonical_decimal("0.82") == "0.82"
    assert canonical_decimal(7) == "7"
    assert canonical_decimal(Decimal("1.2300")) == "1.23"
    with pytest.raises(ValueError):
        canonical_decimal(0.82)
    with pytest.raises(ValueError):
        canonical_decimal("1e-3")
    with pytest.raises(ValueError):
        canonical_decimal("1.20")


def test_score_builder_enforces_transform_and_threshold_contracts():
    score = build_score_interpretation(
        score_value="82",
        scale_kind="bounded_numeric",
        transform="percentage",
        minimum="0",
        maximum="100",
        score_direction="higher_is_favorable",
        score_source="provider/model",
        threshold_value="75",
        threshold_meaning="approve above threshold",
    )
    assert score["score_scale"]["maximum"] == "100"
    with pytest.raises(ValueError):
        build_score_interpretation(
            score_value="0.82",
            scale_kind="bounded_numeric",
            transform="probability",
            minimum="0",
            maximum="100",
            score_direction="higher_is_riskier",
            score_source="provider/model",
        )
    with pytest.raises(ValueError):
        build_score_interpretation(
            score_value="0.82",
            scale_kind="bounded_numeric",
            minimum="0",
            maximum="1",
            score_direction="higher_is_riskier",
            score_source="provider/model",
            threshold_value="0.75",
        )


def test_policy_and_evidence_helpers_enforce_declared_context_rules():
    with pytest.raises(ValueError):
        build_policy_context(
            policy_id="policy-1",
            policy_name="Policy",
            policy_version="1",
            policy_digest="sha256:" + "a" * 64,
            effective_at="2026-07-29T12:00:00.000000Z",
            capture_mode="FULL_PAYLOAD",
        )
    with pytest.raises(ValueError):
        build_evidence_assessment(evidence_completeness="partial")
    with pytest.raises(ValueError):
        build_evidence_assessment(
            evidence_completeness="complete",
            missing_evidence_flags=[
                build_evidence_flag(code="MISSING", description="Missing input")
            ],
        )


def test_request_builder_rejects_duplicate_policy_versions_and_floats():
    request = _request()
    duplicate = dict(request)
    duplicate["policy_contexts"] = request["policy_contexts"] * 2
    with pytest.raises(ValueError):
        build_ai_decision_request(
            decision_type=request["decision_type"],
            declared_outcome=request["declared_outcome"],
            decision_reason=request["decision_reason"],
            decided_at=request["decided_at"],
            evidence_assessment=request["evidence_assessment"],
            actor=request["actor"],
            privacy=request["privacy"],
            policy_contexts=duplicate["policy_contexts"],
        )

    request_with_float = dict(request)
    request_with_float["evidence_references"] = [{"weight": 0.5}]
    transport = FakeTransport([])
    client = AIDecisionClient(base_url="https://example.test", api_key="ak", transport=transport)
    with pytest.raises(Exception):
        client.create(request_with_float)
    assert transport.calls == []


def test_create_list_get_seal_verify_use_expected_endpoints():
    transport = FakeTransport(
        [
            _json_response(201, _record()),
            _json_response(200, [{"record_id": "asd_test"}]),
            _json_response(200, _record()),
            _json_response(200, {"record": {"seal": {"seal_state": "SEALED"}}}),
            _json_response(200, {"status": "VALID"}),
        ]
    )
    client = AIDecisionClient(base_url="https://example.test", api_key="ak", transport=transport)
    created = client.create(_request(), idempotency_key="create-decision-1")
    assert created["record"]["subject"]["record_id"] == "asd_test"
    assert client.list(limit=25, offset=5) == [{"record_id": "asd_test"}]
    assert client.get("asd_test")["decision_id"] == "decision_test"
    assert client.seal("asd_test", idempotency_key="seal-decision-1")["record"]["seal"]["seal_state"] == "SEALED"
    assert client.verify("asd_test")["status"] == "VALID"
    assert [call["endpoint"] for call in transport.calls] == [
        "/v1/ai-decisions",
        "/v1/ai-decisions?limit=25&offset=5",
        "/v1/ai-decisions/asd_test",
        "/v1/ai-decisions/asd_test/seal",
        "/v1/ai-decisions/asd_test/verify",
    ]
    assert json.loads(transport.calls[0]["body"].decode("utf-8"))["score_interpretation"]["score_value"] == "0.82"


def test_api_error_preserves_structured_error():
    transport = FakeTransport(
        [
            _json_response(
                409,
                {
                    "error": {
                        "code": "AI_DECISION_CONFLICT",
                        "detail": "conflict",
                        "retry_permitted": False,
                    }
                },
            )
        ]
    )
    client = AIDecisionClient(base_url="https://example.test", api_key="ak", transport=transport)
    with pytest.raises(AIDecisionAPIError) as exc_info:
        client.get("asd_conflict")
    assert exc_info.value.status == 409
    assert exc_info.value.code == "AI_DECISION_CONFLICT"


def test_download_bundle_is_atomic_and_rejects_non_zip(tmp_path: Path):
    good = FakeTransport([AIDecisionHttpResponse(200, b"PK\x03\x04bundle", {})])
    client = AIDecisionClient(base_url="https://example.test", api_key="ak", transport=good)
    target = client.download_bundle("asd_1", tmp_path)
    assert target.name == "AuroraSeal_AIDecision_asd_1.zip"
    assert target.read_bytes().startswith(b"PK")

    bad = FakeTransport([AIDecisionHttpResponse(200, b"not-a-zip", {})])
    client = AIDecisionClient(base_url="https://example.test", api_key="ak", transport=bad)
    with pytest.raises(AIDecisionTransportError):
        client.download_bundle("asd_2", tmp_path / "bad.zip")


def test_score_builder_matches_numeric_and_label_scale_contracts():
    ordinal = build_score_interpretation(
        score_value="high",
        scale_kind="ordinal",
        labels=["low", "medium", "high"],
        score_direction="higher_is_riskier",
        score_source="provider/risk-band",
    )
    assert ordinal["score_value"] == "high"
    assert ordinal["score_scale"]["labels"] == ["low", "medium", "high"]

    with pytest.raises(ValueError, match="outside the declared score scale"):
        build_score_interpretation(
            score_value="1.2",
            scale_kind="bounded_numeric",
            minimum="0",
            maximum="1",
            score_direction="higher_is_riskier",
            score_source="provider/model",
        )
    with pytest.raises(ValueError, match="not in the declared score scale labels"):
        build_score_interpretation(
            score_value="critical",
            scale_kind="categorical",
            labels=["allow", "review", "deny"],
            score_direction="non_monotonic",
            score_source="provider/decision-class",
        )
    with pytest.raises(ValueError, match="do not accept thresholds"):
        build_score_interpretation(
            score_value="review",
            scale_kind="categorical",
            labels=["allow", "review", "deny"],
            score_direction="non_monotonic",
            score_source="provider/decision-class",
            threshold_value="1",
            threshold_meaning="not valid for a categorical scale",
        )


def test_policy_helper_rejects_invalid_calendar_timestamp():
    with pytest.raises(ValueError, match="not a valid UTC timestamp"):
        build_policy_context(
            policy_id="policy-1",
            policy_name="Policy",
            policy_version="1",
            policy_digest="sha256:" + "a" * 64,
            effective_at="2026-02-30T12:00:00.000000Z",
        )