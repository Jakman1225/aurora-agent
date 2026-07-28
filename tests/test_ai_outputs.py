from __future__ import annotations

import json
from pathlib import Path

import pytest

from aurora_agent import (
    AIOutputAPIError,
    AIOutputClient,
    AIOutputTransportError,
    CanonicalizationError,
    content_digest,
)
from aurora_agent.ai_outputs import AIOutputHttpResponse


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, **kwargs):
        self.calls.append(kwargs)
        if not self.responses:
            raise AssertionError("unexpected transport request")
        return self.responses.pop(0)


def _json_response(status: int, payload: dict) -> AIOutputHttpResponse:
    return AIOutputHttpResponse(
        status=status,
        body=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )


def _created_record(record_id: str = "ase_test") -> dict:
    return {
        "record": {
            "subject": {
                "record_id": record_id,
                "record_type": "ai_output",
                "payload": {"output_id": "output_test"},
            },
            "seal": {"seal_state": "DIGESTED"},
        },
        "output_id": "output_test",
        "verification_url": f"/public/verify/ai-output/{record_id}",
    }


def test_full_payload_helper_rejects_floats_before_transport():
    transport = FakeTransport([])
    client = AIOutputClient(base_url="https://example.test", api_key="ak", transport=transport)
    with pytest.raises(CanonicalizationError):
        client.create_full_payload(
            input_content={"temperature": 0.1},
            output_content={"label": "ok"},
            model_provider="provider",
            model_name="model",
            actor={"actor_type": "service", "actor_id": "service_1"},
        )
    assert transport.calls == []


def test_create_full_payload_sends_canonical_request_and_idempotency_key():
    transport = FakeTransport([_json_response(201, _created_record())])
    client = AIOutputClient(base_url="https://example.test", api_key="ak", transport=transport)
    result = client.create_full_payload(
        input_content={"prompt": "hello"},
        output_content={"answer": "world"},
        model_provider="provider",
        model_name="model",
        actor={"actor_type": "service", "actor_id": "service_1"},
        generated_at="2026-07-28T12:00:00.000000Z",
        inference_parameters={"max_tokens": 128},
    )
    assert result["record"]["subject"]["record_id"] == "ase_test"
    call = transport.calls[0]
    assert call["endpoint"] == "/v1/ai-outputs"
    assert call["idempotency_key"].startswith("auroraseal.ai-output.create:")
    payload = json.loads(call["body"].decode("utf-8"))
    assert payload["capture_mode"] == "FULL_PAYLOAD"
    assert payload["privacy"]["public_display_mode"] == "metadata_only"


def test_digest_only_helper_preserves_commitments():
    transport = FakeTransport([_json_response(201, _created_record())])
    client = AIOutputClient(base_url="https://example.test", api_key="ak", transport=transport)
    input_digest = "sha256:" + "a" * 64
    output_digest = "sha256:" + "b" * 64
    client.create_digest_only(
        input_digest=input_digest,
        output_digest=output_digest,
        model_provider="provider",
        model_name="model",
        actor={"actor_type": "service", "actor_id": "service_1"},
        generated_at="2026-07-28T12:00:00.000000Z",
    )
    payload = json.loads(transport.calls[0]["body"].decode("utf-8"))
    assert payload["input_digest"] == input_digest
    assert payload["output_digest"] == output_digest
    assert "input_content" not in payload
    assert "output_content" not in payload


def test_get_seal_and_verify_use_expected_endpoints():
    transport = FakeTransport(
        [
            _json_response(200, _created_record("ase_1")),
            _json_response(200, {"sealed": True}),
            _json_response(200, {"status": "VALID"}),
        ]
    )
    client = AIOutputClient(base_url="https://example.test", api_key="ak", transport=transport)
    assert client.get("ase_1")["record"]["subject"]["record_id"] == "ase_1"
    assert client.seal("ase_1", idempotency_key="seal-key-0001")["sealed"] is True
    assert client.verify("ase_1")["status"] == "VALID"
    assert [call["endpoint"] for call in transport.calls] == [
        "/v1/ai-outputs/ase_1",
        "/v1/ai-outputs/ase_1/seal",
        "/v1/ai-outputs/ase_1/verify",
    ]


def test_api_error_preserves_structured_error():
    transport = FakeTransport(
        [
            _json_response(
                409,
                {
                    "error": {
                        "code": "AI_OUTPUT_CONFLICT",
                        "detail": "conflict",
                        "retry_permitted": False,
                    }
                },
            )
        ]
    )
    client = AIOutputClient(base_url="https://example.test", api_key="ak", transport=transport)
    with pytest.raises(AIOutputAPIError) as exc_info:
        client.get("ase_conflict")
    assert exc_info.value.status == 409
    assert exc_info.value.code == "AI_OUTPUT_CONFLICT"


def test_download_bundle_is_atomic_and_rejects_non_zip(tmp_path: Path):
    good = FakeTransport([AIOutputHttpResponse(200, b"PK\x03\x04bundle", {})])
    client = AIOutputClient(base_url="https://example.test", api_key="ak", transport=good)
    target = client.download_bundle("ase_1", tmp_path)
    assert target.name == "AuroraSeal_AIOutput_ase_1.zip"
    assert target.read_bytes().startswith(b"PK")

    bad = FakeTransport([AIOutputHttpResponse(200, b"not-a-zip", {})])
    client = AIOutputClient(base_url="https://example.test", api_key="ak", transport=bad)
    with pytest.raises(AIOutputTransportError):
        client.download_bundle("ase_2", tmp_path / "bad.zip")


def test_content_digest_matches_canonical_commitment():
    assert content_digest({"b": 2, "a": 1}) == content_digest({"a": 1, "b": 2})