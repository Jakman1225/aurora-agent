from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import unquote

import pytest

from aurora_agent import __version__
from aurora_agent.command import main
from aurora_agent.ingestion_http import HttpResponse
from aurora_agent.quickstart import QuickstartRunner


class QuickstartTransport:
    def __init__(self):
        self.calls = []
        self.run_id = None
        self.atp_id = "ATP-QUICKSTART-TEST"
        self.graph_id = "ingest:run_quickstart_test"

    def request(self, *, method, endpoint, body, idempotency_key):
        self.calls.append((method, endpoint, body, idempotency_key))
        if endpoint == "/v1/audit-records":
            return self._response(201, {"id": "rec-test", "atp_id": self.atp_id, "seal_state": "SEALED"})
        if endpoint == "/v1/evidence/runs" and method == "POST":
            payload = json.loads(body)
            self.run_id = payload["run_id"]
            return self._response(201, {"idempotency_status": "CREATED"})
        if endpoint.endswith("/events"):
            return self._response(201, {"idempotency_status": "CREATED"})
        if endpoint.endswith("/finalize"):
            return self._response(201, {"idempotency_status": "CREATED"})
        if endpoint.endswith("/verify"):
            return self._response(200, {"run_id": self.run_id, "state": "FINALIZED", "verdict": "VALID", "issues": []})
        if endpoint == f"/v1/evidence/runs/{self.run_id}" and method == "GET":
            return self._response(200, {"run_id": self.run_id, "state": "FINALIZED", "graph_id": self.graph_id, "graph_manifest_digest": "sha256:" + "a" * 64})
        return self._response(404, {"detail": f"unexpected {method} {endpoint}"})

    @staticmethod
    def _response(status, payload):
        return HttpResponse(status=status, body=json.dumps(payload).encode(), headers={})


def test_quickstart_creates_valid_graph_without_persisting_api_key(tmp_path: Path):
    transport = QuickstartTransport()
    runner = QuickstartRunner(
        api_key="ak_live_secret_must_not_persist",
        base_url="https://api.example.test",
        frontend_url="https://ui.example.test",
        workspace=tmp_path,
        transport=transport,
        id_factory=lambda: "test",
    )
    result = runner.run()
    assert result.acceptance == "PASS"
    assert result.verification_verdict == "VALID"
    assert result.executor_invocation_count == 1
    assert result.operation_count == 1
    assert result.capture_mode == "DIGEST_ONLY"
    assert "graph=ingest%3Arun_quickstart_test" in result.viewer_url
    assert unquote(result.viewer_url).endswith("?graph=ingest:run_quickstart_test#compositional-evidence")
    event_types = [
        json.loads(body)["event_type"]
        for method, endpoint, body, _ in transport.calls
        if method == "POST" and endpoint.endswith("/events")
    ]
    assert event_types == [
        "authorization",
        "tool_request",
        "tool_execution",
        "tool_outcome",
        "final_decision",
    ]
    audit_create = next(
        call for call in transport.calls if call[1] == "/v1/audit-records"
    )
    assert audit_create[3] == (
        "aurora-agent.quickstart.audit-record:op_quickstart_test"
    )
    run_create = next(
        call
        for call in transport.calls
        if call[0] == "POST" and call[1] == "/v1/evidence/runs"
    )
    assert json.loads(run_create[2])["release_id"] == f"aurora-agent-{__version__}"
    workspace_bytes = b"".join(path.read_bytes() for path in tmp_path.rglob("*") if path.is_file())
    assert b"ak_live_secret_must_not_persist" not in workspace_bytes


def test_command_init_writes_no_secret(tmp_path: Path, capsys):
    assert main(["init", "--directory", str(tmp_path)]) == 0
    text = (tmp_path / "AURORA_QUICKSTART.txt").read_text()
    assert "AURORA_API_KEY" in text
    assert "ak_live" not in text
    assert "Next:" in capsys.readouterr().out


@pytest.mark.parametrize("create_status", [201, 202])
def test_quickstart_waits_for_the_same_record(monkeypatch, tmp_path, create_status):
    from aurora_agent import quickstart

    monkeypatch.setattr(quickstart.time, "sleep", lambda seconds: None)

    class PendingTransport(QuickstartTransport):
        checks = 0

        def request(self, *, method, endpoint, body, idempotency_key):
            if endpoint == "/v1/audit-records":
                self.calls.append((method, endpoint, body, idempotency_key))
                return self._response(create_status, {
                    "id": "rec-test", "atp_id": self.atp_id, "seal_state": "TIMESTAMP_PENDING",
                })
            if endpoint == "/v1/audit-records/rec-test":
                self.calls.append((method, endpoint, body, idempotency_key))
                self.checks += 1
                return self._response(200, {
                    "id": "rec-test", "atp_id": self.atp_id,
                    "seal_state": "SEALED" if self.checks == 2 else "TIMESTAMP_PENDING",
                })
            assert self.checks == 2
            return super().request(method=method, endpoint=endpoint, body=body,
                                   idempotency_key=idempotency_key)

    transport = PendingTransport()
    assert QuickstartRunner(api_key="local-test", workspace=tmp_path, transport=transport,
                            id_factory=lambda: "test").run().acceptance == "PASS"
    assert transport.calls[0][3] == "aurora-agent.quickstart.audit-record:op_quickstart_test"
    assert [call[1] for call in transport.calls[:4]] == [
        "/v1/audit-records", "/v1/audit-records/rec-test",
        "/v1/audit-records/rec-test", "/v1/evidence/runs",
    ]


def test_quickstart_rejects_old_internal_only_record(tmp_path):
    from aurora_agent.quickstart import QuickstartError

    class LegacyTransport(QuickstartTransport):
        def request(self, *, method, endpoint, body, idempotency_key):
            assert endpoint == "/v1/audit-records"
            return self._response(201, {
                "id": "rec-test", "atp_id": self.atp_id, "sealed_at": "2026-11-16T00:00:00Z",
                "tsa_response_received": False, "tsa_status": "internal_timestamp_only",
            })

    with pytest.raises(QuickstartError, match="verified external timestamp"):
        QuickstartRunner(api_key="local-test", workspace=tmp_path, transport=LegacyTransport(),
                         id_factory=lambda: "test").run()


def test_quickstart_timeout_has_no_evidence_run(monkeypatch, tmp_path):
    from aurora_agent import quickstart

    monkeypatch.setattr(quickstart, "TIMESTAMP_WAIT_SECONDS", 0)

    class PendingTransport(QuickstartTransport):
        def request(self, *, method, endpoint, body, idempotency_key):
            assert endpoint == "/v1/audit-records"
            return self._response(202, {
                "id": "rec-test", "atp_id": self.atp_id, "seal_state": "TIMESTAMP_PENDING",
            })

    with pytest.raises(quickstart.QuickstartError, match="rec-test.*still TIMESTAMP_PENDING"):
        QuickstartRunner(api_key="local-test", workspace=tmp_path, transport=PendingTransport(),
                         id_factory=lambda: "test").run()
