from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aurora_agent import IngestionClient, JAKROWD3IngestionObserver
from aurora_agent.ingestion_http import HttpResponse, IngestionTransportError
from evidence_contract.approval_d2_io import atomic_write_json
from evidence_contract.approval_d3_operation import count_operations
from evidence_contract.approval_process_c_execute import run_d3_execution
from evidence_contract.approval_store import ApprovalStore
from evidence_contract.production_integration import (
    ControlledD3Executor,
    MODE_FAIL_AFTER_LOCAL_OPERATION,
    MODE_FAIL_BEFORE_CONSEQUENCE,
)


class MutableTransport:
    def __init__(self, responses=()):
        self.responses = list(responses)
        self.calls = []

    def request(self, *, method, endpoint, body, idempotency_key):
        self.calls.append((method, endpoint, body, idempotency_key))
        if not self.responses:
            raise IngestionTransportError("offline")
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def _ok(status=201, body=None):
    return HttpResponse(
        status=status,
        body=json.dumps(body or {"idempotency_status": "CREATED"}).encode(),
        headers={},
    )


def _prepare(tmp_path: Path):
    suffix = uuid.uuid4().hex
    run_id = f"run_jakrow_integration_{suffix}"
    action_id = f"act_jakrow_integration_{suffix}"
    approval_db = tmp_path / "approval.db"
    operations_db = tmp_path / "operations.db"
    request_path = tmp_path / "request.json"
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime(
        "%Y-%m-%dT%H:%M:%S.%fZ"
    )
    request = {
        "schema_version": "jakrow.approval-process.request.v0.1",
        "run_id": run_id,
        "action_id": action_id,
        "tool_name": "execute_operation",
        "target": "acct:integration-secret-target",
        "amount": 1,
        "expires_at": expires_at,
    }
    atomic_write_json(request_path, request)
    store = ApprovalStore(approval_db)
    bundle = store.create_request(
        arguments={"target": request["target"], "amount": request["amount"]},
        run_id=run_id,
        action_id=action_id,
        expires_at=expires_at,
    )
    store.authorize(approval_ref=bundle.request.approval_ref)
    return run_id, approval_db, operations_db, request_path, bundle.request.approval_ref


def _client(tmp_path: Path, transport: MutableTransport):
    return IngestionClient(
        base_url="https://example.invalid",
        api_key="ak_live_must_not_persist",
        outbox_path=tmp_path / "outbox.db",
        transport=transport,
        clock=lambda: "2026-07-21T00:00:00.000000Z",
    )


def test_real_d3_success_queues_finalized_digest_only_graph_input(tmp_path: Path):
    run_id, adb, odb, request, approval_ref = _prepare(tmp_path)
    transport = MutableTransport([_ok() for _ in range(7)])
    client = _client(tmp_path, transport)
    run = client.start_run(atp_id="ATP-TEST", run_id=run_id)
    observer = JAKROWD3IngestionObserver(run)
    executor = ControlledD3Executor(
        ledger_path=tmp_path / "executor.db", operation_key="success"
    )

    code, result = run_d3_execution(
        approval_db=str(adb),
        operations_db=str(odb),
        approval_ref=approval_ref,
        request_path=str(request),
        executor=executor,
        ingestion_observer=observer,
    )
    assert code == 0
    assert executor.invocation_count == 1
    assert count_operations(odb) == 1
    assert result["ingestion_capture"]["terminal"]["finalize_request_key"]

    event_bodies = [
        json.loads(item.request_bytes)
        for item in run.requests()
        if "/events" in item.endpoint
    ]
    assert [item["event_type"] for item in event_bodies] == [
        "authorization",
        "tool_request",
        "tool_execution",
        "tool_outcome",
        "final_decision",
    ]
    assert all(item["payload_input"]["kind"] == "DIGEST" for item in event_bodies)
    raw_db = (tmp_path / "outbox.db").read_bytes()
    assert b"integration-secret-target" not in raw_db
    assert b"ak_live_must_not_persist" not in raw_db
    assert all(item.state == "ACKNOWLEDGED" for item in client.flush())


def test_network_outage_replays_evidence_without_reexecuting_consequence(tmp_path: Path):
    run_id, adb, odb, request, approval_ref = _prepare(tmp_path)
    transport = MutableTransport([IngestionTransportError("offline")])
    client = _client(tmp_path, transport)
    run = client.start_run(atp_id="ATP-TEST", run_id=run_id)
    observer = JAKROWD3IngestionObserver(run)
    executor = ControlledD3Executor(
        ledger_path=tmp_path / "executor.db", operation_key="offline"
    )
    code, _ = run_d3_execution(
        approval_db=str(adb),
        operations_db=str(odb),
        approval_ref=approval_ref,
        request_path=str(request),
        executor=executor,
        ingestion_observer=observer,
    )
    assert code == 0
    first = client.flush()
    assert first[0].state == "PENDING"
    assert executor.invocation_count == 1
    assert count_operations(odb) == 1

    transport.responses.extend([_ok() for _ in range(7)])
    assert all(item.state == "ACKNOWLEDGED" for item in client.flush())
    replay_code, replay = run_d3_execution(
        approval_db=str(adb),
        operations_db=str(odb),
        approval_ref=approval_ref,
        request_path=str(request),
        executor=executor,
        ingestion_observer=JAKROWD3IngestionObserver(run),
    )
    assert replay_code == 20
    assert replay["rejection"] == "REPLAY"
    assert executor.invocation_count == 1
    assert count_operations(odb) == 1


class _FailAfterOutcomeObserver(JAKROWD3IngestionObserver):
    def _queue_final_decision(self, **kwargs):
        raise RuntimeError("injected observer failure after durable consequence")


def test_post_consequence_observer_failure_recovers_without_duplicate_nodes(tmp_path: Path):
    run_id, adb, odb, request, approval_ref = _prepare(tmp_path)
    transport = MutableTransport([_ok() for _ in range(7)])
    client = _client(tmp_path, transport)
    run = client.start_run(atp_id="ATP-TEST", run_id=run_id)
    executor = ControlledD3Executor(
        ledger_path=tmp_path / "executor.db", operation_key="post-observer"
    )
    code, result = run_d3_execution(
        approval_db=str(adb),
        operations_db=str(odb),
        approval_ref=approval_ref,
        request_path=str(request),
        executor=executor,
        ingestion_observer=_FailAfterOutcomeObserver(run),
    )
    assert code == 0
    assert result["ingestion_capture"]["terminal"]["status"] == "OUTBOX_ERROR"
    assert executor.invocation_count == 1

    recovery_code, recovery = run_d3_execution(
        approval_db=str(adb),
        operations_db=str(odb),
        approval_ref=approval_ref,
        request_path=str(request),
        executor=executor,
        ingestion_observer=JAKROWD3IngestionObserver(run),
    )
    assert recovery_code == 20
    assert recovery["rejection"] == "REPLAY"
    event_items = [item for item in run.requests() if "/events" in item.endpoint]
    ids = [json.loads(item.request_bytes)["event_id"] for item in event_items]
    assert len(ids) == 5
    assert len(ids) == len(set(ids))
    assert executor.invocation_count == 1
    assert count_operations(odb) == 1
    assert all(item.state == "ACKNOWLEDGED" for item in client.flush())


def test_started_after_operation_without_terminal_becomes_unknown_graph_input(tmp_path: Path):
    run_id, adb, odb, request, approval_ref = _prepare(tmp_path)
    transport = MutableTransport([_ok() for _ in range(7)])
    client = _client(tmp_path, transport)
    run = client.start_run(atp_id="ATP-TEST", run_id=run_id)
    executor = ControlledD3Executor(
        ledger_path=tmp_path / "executor.db",
        operation_key="unknown",
        mode=MODE_FAIL_AFTER_LOCAL_OPERATION,
    )
    code, result = run_d3_execution(
        approval_db=str(adb),
        operations_db=str(odb),
        approval_ref=approval_ref,
        request_path=str(request),
        executor=executor,
        ingestion_observer=JAKROWD3IngestionObserver(run),
    )
    assert code == 30
    assert result["status"] == "OUTCOME_UNKNOWN"
    assert executor.invocation_count == 1
    assert count_operations(odb) == 1
    event_types = [
        json.loads(item.request_bytes)["event_type"]
        for item in run.requests()
        if "/events" in item.endpoint
    ]
    assert event_types[-2:] == ["runtime_failure", "final_decision"]
    assert all(item.state == "ACKNOWLEDGED" for item in client.flush())


def test_durable_terminal_recovery_reconstructs_missing_pre_events(tmp_path: Path):
    run_id, adb, odb, request, approval_ref = _prepare(tmp_path)
    executor = ControlledD3Executor(
        ledger_path=tmp_path / "executor.db", operation_key="fresh-outbox-recovery"
    )

    code, _ = run_d3_execution(
        approval_db=str(adb),
        operations_db=str(odb),
        approval_ref=approval_ref,
        request_path=str(request),
        executor=executor,
        ingestion_observer=None,
    )
    assert code == 0
    assert executor.invocation_count == 1
    assert count_operations(odb) == 1

    transport = MutableTransport([_ok() for _ in range(7)])
    client = _client(tmp_path, transport)
    run = client.start_run(atp_id="ATP-TEST", run_id=run_id)
    recovery_code, recovery = run_d3_execution(
        approval_db=str(adb),
        operations_db=str(odb),
        approval_ref=approval_ref,
        request_path=str(request),
        executor=executor,
        ingestion_observer=JAKROWD3IngestionObserver(run),
    )
    assert recovery_code == 20
    assert recovery["rejection"] == "REPLAY"
    assert recovery["ingestion_capture"]["status"] == "QUEUED_LOCALLY"
    assert executor.invocation_count == 1
    assert count_operations(odb) == 1

    event_bodies = [
        json.loads(item.request_bytes)
        for item in run.requests()
        if "/events" in item.endpoint
    ]
    assert [item["event_type"] for item in event_bodies] == [
        "authorization",
        "tool_request",
        "tool_execution",
        "tool_outcome",
        "final_decision",
    ]
    assert event_bodies[2]["payload_input"]["kind"] == "DIGEST"
    assert all(item.state == "ACKNOWLEDGED" for item in client.flush())


def test_real_outbox_failure_before_consequence_invokes_no_executor(
    tmp_path: Path, monkeypatch
):
    run_id, adb, odb, request, approval_ref = _prepare(tmp_path)
    transport = MutableTransport([_ok() for _ in range(6)])
    client = _client(tmp_path, transport)
    run = client.start_run(atp_id="ATP-TEST", run_id=run_id)
    observer = JAKROWD3IngestionObserver(run)
    executor = ControlledD3Executor(
        ledger_path=tmp_path / "executor.db", operation_key="outbox-failure"
    )

    original_append = client.outbox.append_event

    def fail_append(**kwargs):
        raise OSError("injected local outbox write failure")

    monkeypatch.setattr(client.outbox, "append_event", fail_append)
    code, result = run_d3_execution(
        approval_db=str(adb),
        operations_db=str(odb),
        approval_ref=approval_ref,
        request_path=str(request),
        executor=executor,
        ingestion_observer=observer,
    )
    assert code == 31
    assert result["status"] == "EXECUTION_ABORTED_BEFORE_CONSEQUENCE"
    assert executor.invocation_count == 0
    assert count_operations(odb) == 0

    monkeypatch.setattr(client.outbox, "append_event", original_append)
    recovery_code, recovery = run_d3_execution(
        approval_db=str(adb),
        operations_db=str(odb),
        approval_ref=approval_ref,
        request_path=str(request),
        executor=executor,
        ingestion_observer=JAKROWD3IngestionObserver(run),
    )
    assert recovery_code == 31
    assert recovery["status"] == "EXECUTION_ABORTED_BEFORE_CONSEQUENCE"
    assert executor.invocation_count == 0
    assert count_operations(odb) == 0
    event_types = [
        json.loads(item.request_bytes)["event_type"]
        for item in run.requests()
        if "/events" in item.endpoint
    ]
    assert event_types == [
        "authorization",
        "tool_request",
        "tool_outcome",
        "final_decision",
    ]
    assert all(item.state == "ACKNOWLEDGED" for item in client.flush())


def test_declared_preconsequence_failure_keeps_graph_connected(tmp_path: Path):
    run_id, adb, odb, request, approval_ref = _prepare(tmp_path)
    transport = MutableTransport([_ok() for _ in range(7)])
    client = _client(tmp_path, transport)
    run = client.start_run(atp_id="ATP-TEST", run_id=run_id)
    executor = ControlledD3Executor(
        ledger_path=tmp_path / "executor.db",
        operation_key="declared-preconsequence",
        mode=MODE_FAIL_BEFORE_CONSEQUENCE,
    )
    code, result = run_d3_execution(
        approval_db=str(adb),
        operations_db=str(odb),
        approval_ref=approval_ref,
        request_path=str(request),
        executor=executor,
        ingestion_observer=JAKROWD3IngestionObserver(run),
    )
    assert code == 31
    assert result["status"] == "EXECUTION_ABORTED_BEFORE_CONSEQUENCE"
    assert executor.invocation_count == 1
    assert count_operations(odb) == 0

    events = [
        json.loads(item.request_bytes)
        for item in run.requests()
        if "/events" in item.endpoint
    ]
    assert [event["event_type"] for event in events] == [
        "authorization",
        "tool_request",
        "tool_execution",
        "tool_outcome",
        "final_decision",
    ]
    assert events[3]["parent_event_ids"] == [events[2]["event_id"]]
    assert events[4]["parent_event_ids"] == [events[3]["event_id"]]
    assert all(item.state == "ACKNOWLEDGED" for item in client.flush())
