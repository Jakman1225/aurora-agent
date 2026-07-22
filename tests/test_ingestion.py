from __future__ import annotations

import json

import pytest

from aurora_agent import ClaudeAgentCaptureAdapter, IngestionClient
from aurora_agent.ingestion_constants import (
    STATE_ACKNOWLEDGED,
    STATE_PENDING,
    STATE_SUBMITTING,
)
from aurora_agent.ingestion_http import HttpResponse, IngestionTransportError
from aurora_agent.exceptions import LifecycleError
from aurora_agent.ingestion_outbox import IngestionOutbox, OutboxConflict


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, *, method, endpoint, body, idempotency_key):
        self.calls.append((method, endpoint, body, idempotency_key))
        item = self.responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def ok(status=201, body=None):
    return HttpResponse(status, json.dumps(body or {"ok": True}).encode(), {})


def client(tmp_path, responses=()):
    fake = FakeTransport(responses)
    counters = {}

    def next_id(prefix):
        counters[prefix] = counters.get(prefix, 0) + 1
        return f"{prefix}_{counters[prefix]}"

    result = IngestionClient(
        base_url="https://example.invalid",
        api_key="ak_live_not_persisted",
        outbox_path=tmp_path / "outbox.db",
        transport=fake,
        clock=lambda: "2026-07-21T00:00:00.000000Z",
        id_factory=next_id,
    )
    return result, fake


def test_digest_only_outbox_never_contains_raw_payload_or_api_key(tmp_path):
    sdk, _ = client(tmp_path)
    run = sdk.start_run(atp_id="ATP-TEST", run_id="run-test")
    run.capture("prompt", {"secret": "raw-prompt"}, event_id="prompt-1")
    raw_db = (tmp_path / "outbox.db").read_bytes()
    assert b"raw-prompt" not in raw_db
    assert b"ak_live_not_persisted" not in raw_db
    event_body = json.loads(run.requests()[1].request_bytes)
    assert event_body["payload_input"]["kind"] == "DIGEST"
    assert event_body["payload_input"]["value"].startswith("sha256:")


def test_redacted_mode_persists_redacted_value_only(tmp_path):
    sdk, _ = client(tmp_path)
    run = sdk.start_run(
        atp_id="ATP-TEST", run_id="run-redacted", capture_mode="REDACTED"
    )
    run.capture(
        "prompt",
        {"secret": "never-store"},
        redacted_payload={"secret": "[REDACTED]"},
        redacted_payload_present=True,
        event_id="prompt-1",
    )
    raw_db = (tmp_path / "outbox.db").read_bytes()
    assert b"never-store" not in raw_db
    assert b"REDACTED" in raw_db


def test_full_payload_mode_is_explicit_and_persists_payload(tmp_path):
    sdk, _ = client(tmp_path)
    run = sdk.start_run(
        atp_id="ATP-TEST", run_id="run-full", capture_mode="FULL_PAYLOAD"
    )
    run.capture("prompt", {"value": "stored"}, event_id="prompt-1")
    assert b"stored" in (tmp_path / "outbox.db").read_bytes()


def test_outbox_flushes_run_events_finalize_in_order(tmp_path):
    sdk, fake = client(tmp_path, [ok(), ok(), ok(), ok()])
    run = sdk.start_run(atp_id="ATP-TEST", run_id="run-order")
    first = run.capture("prompt", {"p": 1}, event_id="prompt-1")
    second = run.capture(
        "final_decision", {"decision": "APPROVED"}, event_id="decision-1"
    )
    assert first == "prompt-1" and second == "decision-1"
    run.finalize()
    completed = sdk.flush()
    assert [item.state for item in completed] == [STATE_ACKNOWLEDGED] * 4
    assert [call[1] for call in fake.calls] == [
        "/v1/evidence/runs",
        "/v1/evidence/runs/run-order/events",
        "/v1/evidence/runs/run-order/events",
        "/v1/evidence/runs/run-order/finalize",
    ]
    event1 = json.loads(fake.calls[1][2])
    event2 = json.loads(fake.calls[2][2])
    assert event1["sequence"] == 0
    assert event2["sequence"] == 1
    assert event2["parent_event_ids"] == ["prompt-1"]


def test_transport_failure_returns_claim_to_pending(tmp_path):
    sdk, _ = client(tmp_path, [IngestionTransportError("offline")])
    sdk.start_run(atp_id="ATP-TEST", run_id="run-offline")
    completed = sdk.flush()
    assert completed[0].state == STATE_PENDING
    assert IngestionOutbox(tmp_path / "outbox.db").items()[0].state == STATE_PENDING


def test_crash_recovery_resets_submitting(tmp_path):
    sdk, _ = client(tmp_path)
    sdk.start_run(atp_id="ATP-TEST", run_id="run-crash")
    claimed = sdk.outbox.claim_next()
    assert claimed is not None and claimed.state == STATE_SUBMITTING
    assert IngestionOutbox(tmp_path / "outbox.db").recover_submitting() == 1
    assert IngestionOutbox(tmp_path / "outbox.db").items()[0].state == STATE_PENDING


def test_same_local_request_key_with_different_bytes_conflicts(tmp_path):
    box = IngestionOutbox(tmp_path / "outbox.db")
    box.create_run(
        run_id="run-conflict",
        capture_mode="DIGEST_ONLY",
        request_key="run:run-conflict",
        endpoint="/v1/evidence/runs",
        idempotency_key="x" * 20,
        body={"a": 1},
    )
    with pytest.raises(OutboxConflict):
        box.create_run(
            run_id="run-conflict",
            capture_mode="DIGEST_ONLY",
            request_key="run:run-conflict",
            endpoint="/v1/evidence/runs",
            idempotency_key="x" * 20,
            body={"a": 2},
        )


def test_claude_adapter_captures_jakrow_tool_lifecycle(tmp_path):
    sdk, _ = client(tmp_path)
    run = sdk.start_run(atp_id="ATP-TEST", run_id="run-adapter")
    adapter = ClaudeAgentCaptureAdapter(run)
    adapter.prompt({"text": "hello"})
    adapter.model_invocation(model="claude-test")
    adapter.authorization(
        approval_ref="apr_" + "a" * 32,
        authorization_digest="sha256:" + "b" * 64,
        actor="human-1",
    )
    with adapter.execute_operation(
        tool_name="execute_operation",
        arguments={"target": "supplier", "amount": 100},
        operation_ref="op_123456789abc",
        approval_ref="apr_" + "a" * 32,
    ):
        pass
    adapter.tool_outcome(
        {"status": "SUCCEEDED"}, operation_ref="op_123456789abc"
    )
    final = adapter.final_decision({"outcome": "APPROVED"})
    run.finalize(root_event_id=final)
    bodies = [json.loads(item.request_bytes) for item in run.requests()[1:-1]]
    assert [body["event_type"] for body in bodies] == [
        "prompt",
        "model_invocation",
        "authorization",
        "tool_request",
        "tool_execution",
        "tool_outcome",
        "final_decision",
    ]


class _D3Request:
    run_id = "run-jakrow"
    action_id = "act-jakrow"
    tool_name = "execute_operation"
    arguments = {"target": "supplier", "amount": 100}


class _AuthorizedCommitment:
    def to_dict(self):
        return {
            "commitment_id": "cmt_" + "a" * 32,
            "authorization_digest": "sha256:" + "b" * 64,
            "proposal_payload_digest": "sha256:" + "c" * 64,
        }


class _Operation:
    def to_dict(self):
        return {
            "operation_id": "op_123456789abc",
            "status": "SUCCEEDED",
            "outcome_strength": "O0",
            "provider_acknowledgement": "NOT_PRESENT",
        }


class _Terminal:
    def to_dict(self):
        return {
            "terminal_state": "SUCCEEDED",
            "terminal_digest": "sha256:" + "d" * 64,
            "operation_reference": "op_123456789abc",
            "outcome_strength": "O0",
            "provider_acknowledgement": "NOT_PRESENT",
        }


class _Verification:
    accepted = True

    def to_dict(self):
        return {"verdict": "ACCEPTED", "accepted": True}


def test_jakrow_d3_observer_queues_pre_and_terminal_events(tmp_path):
    from aurora_agent import JAKROWD3IngestionObserver

    sdk, _ = client(tmp_path)
    run = sdk.start_run(atp_id="ATP-TEST", run_id="run-d3-observer")
    observer = JAKROWD3IngestionObserver(run)
    pre = observer.before_consequence(
        request=_D3Request(),
        approval_ref="apr_" + "a" * 32,
        authorized_commitment=_AuthorizedCommitment(),
        started_at="2026-07-21T00:00:00.000000Z",
        dispatched_payload_digest="sha256:" + "c" * 64,
    )
    terminal = observer.terminal(
        request=_D3Request(),
        approval_ref="apr_" + "a" * 32,
        operation=_Operation(),
        terminal=_Terminal(),
        verification=_Verification(),
    )
    run.finalize(root_event_id=observer.last_event_id)
    event_bodies = [json.loads(item.request_bytes) for item in run.requests()[1:-1]]
    assert pre["status"] == "QUEUED_LOCALLY"
    assert terminal["status"] == "QUEUED_LOCALLY"
    assert [body["event_type"] for body in event_bodies] == [
        "authorization",
        "tool_request",
        "tool_execution",
        "tool_outcome",
        "final_decision",
    ]
    assert event_bodies[-1]["operation_ref"] == "op_123456789abc"


def test_jakrow_d3_observer_outcome_unknown_is_terminal_local_event(tmp_path):
    from aurora_agent import JAKROWD3IngestionObserver

    sdk, _ = client(tmp_path)
    run = sdk.start_run(atp_id="ATP-TEST", run_id="run-d3-unknown")
    observer = JAKROWD3IngestionObserver(run)
    result = observer.outcome_unknown(
        request=_D3Request(),
        approval_ref="apr_" + "a" * 32,
        verification=_Verification(),
    )
    run.finalize(root_event_id=observer.last_event_id)
    bodies = [json.loads(item.request_bytes) for item in run.requests()[1:-1]]
    assert result["status"] == "QUEUED_LOCALLY"
    assert [body["event_type"] for body in bodies] == [
        "runtime_failure",
        "final_decision",
    ]
    assert bodies[0]["payload_input"]["kind"] == "DIGEST"


def test_jakrow_observer_terminal_recovery_is_locally_idempotent(tmp_path):
    from aurora_agent import JAKROWD3IngestionObserver

    sdk, _ = client(tmp_path)
    run = sdk.start_run(atp_id="ATP-TEST", run_id="run-d3-recovery")
    observer = JAKROWD3IngestionObserver(run)
    observer.before_consequence(
        request=_D3Request(),
        approval_ref="apr_" + "a" * 32,
        authorized_commitment=_AuthorizedCommitment(),
        started_at="2026-07-21T00:00:00.000000Z",
        dispatched_payload_digest="sha256:" + "c" * 64,
    )
    first = observer.terminal(
        request=_D3Request(),
        approval_ref="apr_" + "a" * 32,
        operation=_Operation(),
        terminal=_Terminal(),
        verification=_Verification(),
    )
    second_observer = JAKROWD3IngestionObserver(run)
    second = second_observer.terminal(
        request=_D3Request(),
        approval_ref="apr_" + "a" * 32,
        operation=_Operation(),
        terminal=_Terminal(),
        verification=_Verification(),
    )
    assert first["terminal_event_id"] == second["terminal_event_id"]
    assert first["final_decision_event_id"] == second["final_decision_event_id"]
    event_items = [item for item in run.requests() if "/events" in item.endpoint]
    assert len(event_items) == 5
    assert len({json.loads(item.request_bytes)["event_id"] for item in event_items}) == 5


def test_exact_acknowledged_request_can_be_requeued_without_reconstruction(tmp_path):
    sdk, fake = client(tmp_path, [ok(), ok()])
    sdk.start_run(atp_id="ATP-TEST", run_id="run-requeue")
    first = sdk.flush()
    assert first[0].state == STATE_ACKNOWLEDGED
    before = first[0].request_bytes
    sdk.replay_request("run:run-requeue")
    replay = sdk.flush()
    assert replay[0].state == STATE_ACKNOWLEDGED
    assert replay[0].request_bytes == before
    assert fake.calls[0][2] == fake.calls[1][2]
    assert fake.calls[0][3] == fake.calls[1][3]


def test_existing_event_id_requires_all_semantic_fields_to_match(tmp_path):
    sdk, _ = client(tmp_path)
    run = sdk.start_run(atp_id="ATP-TEST", run_id="run-strict-event-replay")
    event_id = run.capture(
        "authorization",
        {"approval_ref": "apr_" + "a" * 32},
        event_id="evt_strict_semantics",
        parent_event_ids=[],
        authorization_ref="apr_" + "a" * 32,
        actor="reviewer-1",
    )
    assert event_id == "evt_strict_semantics"
    with pytest.raises(LifecycleError, match="different local evidence fields"):
        run.capture(
            "authorization",
            {"approval_ref": "apr_" + "a" * 32},
            event_id="evt_strict_semantics",
            parent_event_ids=[],
            authorization_ref=None,
            actor=None,
        )
