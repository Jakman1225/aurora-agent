from __future__ import annotations

import json
from pathlib import Path

import pytest

from aurora_agent import DataLifecycleAPIError, DataLifecycleClient
from aurora_agent.data_lifecycle import DataLifecycleHttpResponse


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


def response(status, value):
    body = value if isinstance(value, bytes) else json.dumps(value).encode()
    return DataLifecycleHttpResponse(status, body, {})


def client(*responses):
    transport = FakeTransport(responses)
    return DataLifecycleClient(base_url="https://example.test", api_key="ak_live_test", transport=transport), transport


def test_write_surfaces_preserve_exact_payload_and_idempotency():
    sdk, transport = client(response(201, {"object_id": "obj-1"}), response(201, {"event_id": "evt-1"}))
    sdk.seal_object({"object_kind": "RETENTION_POLICY", "subject": {"profile_id": "x"}}, idempotency_key="object-key")
    sdk.append_event({"event_id": "evt-1", "event_type": "RETENTION_ASSIGNED"}, idempotency_key="event-key")
    assert transport.calls[0]["endpoint"] == "/v1/data-lifecycle/objects"
    assert transport.calls[0]["idempotency_key"] == "object-key"
    assert json.loads(transport.calls[0]["body"])["object_kind"] == "RETENTION_POLICY"
    assert transport.calls[1]["endpoint"] == "/v1/data-lifecycle/events"
    assert transport.calls[1]["idempotency_key"] == "event-key"


def test_supporting_artifact_and_intent_routes_are_distinct():
    sdk, transport = client(response(201, {}), response(201, {}), response(200, {"operation_intent_digest": "sha256:" + "a" * 64}))
    sdk.register_artifact({"artifact_kind": "POLICY_SNAPSHOT"}, idempotency_key="artifact-key")
    sdk.register_content_artifact({"content_b64": "AA=="}, idempotency_key="content-key")
    sdk.prepare_operation_intent({"operation_type": "CRYPTOGRAPHIC_ERASURE"})
    assert [call["endpoint"] for call in transport.calls] == [
        "/v1/data-lifecycle/artifacts",
        "/v1/data-lifecycle/content-artifacts",
        "/v1/data-lifecycle/operation-intents/prepare",
    ]
    assert transport.calls[2]["idempotency_key"] is None


def test_projection_binds_exact_target_query():
    sdk, transport = client(response(200, {"availability_status": "AVAILABLE"}))
    result = sdk.projection(record_id="ase-1", record_digest="sha256:" + "b" * 64, record_type="ai_decision")
    assert result["availability_status"] == "AVAILABLE"
    assert transport.calls[0]["endpoint"].startswith("/v1/data-lifecycle/records/ase-1?")
    assert "record_type=ai_decision" in transport.calls[0]["endpoint"]


def test_projection_rejects_noncanonical_target_before_transport():
    sdk, transport = client()
    with pytest.raises(ValueError, match="record_digest"):
        sdk.projection(record_id="ase-1", record_digest="bad", record_type="ai_decision")
    with pytest.raises(ValueError, match="record_type"):
        sdk.projection(record_id="ase-1", record_digest="sha256:" + "b" * 64, record_type="other")
    assert transport.calls == []


def test_bundle_is_written_atomically_and_non_zip_is_rejected(tmp_path: Path):
    sdk, _ = client(response(200, b"PK\x03\x04bundle"))
    target = sdk.download_verification_bundle(record_id="ase-1", record_digest="sha256:" + "c" * 64, record_type="ai_output", destination=tmp_path)
    assert target.name == "AuroraSeal_Data_Lifecycle_ase-1.zip"
    assert target.read_bytes().startswith(b"PK")
    broken, _ = client(response(200, b"not zip"))
    with pytest.raises(Exception, match="non-ZIP"):
        broken.download_verification_bundle(record_id="ase-1", record_digest="sha256:" + "c" * 64, record_type="ai_output", destination=tmp_path / "bad.zip")


def test_structured_server_error_is_preserved():
    sdk, _ = client(response(409, {"error": {"code": "DATA_LIFECYCLE_CONFLICT", "detail": "stale head", "retry_permitted": False}}))
    with pytest.raises(DataLifecycleAPIError) as exc:
        sdk.append_event({"event_id": "evt-1"}, idempotency_key="event-key")
    assert exc.value.status == 409
    assert exc.value.code == "DATA_LIFECYCLE_CONFLICT"