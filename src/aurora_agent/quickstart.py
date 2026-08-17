from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import quote

from ._version import __version__
from .ingestion import IngestionClient
from .ingestion_constants import CAPTURE_DIGEST_ONLY, STATE_ACKNOWLEDGED
from .ingestion_http import IngestionTransportError, UrllibTransport

DEFAULT_API_BASE_URL = "https://aurora-mvp-production.up.railway.app"
DEFAULT_FRONTEND_URL = "https://auroraseal.com"
QUICKSTART_RELEASE_ID = f"aurora-agent-{__version__}"


class QuickstartError(RuntimeError):
    """Raised when the self-serve quickstart cannot complete safely."""


@dataclass(frozen=True)
class QuickstartResult:
    acceptance: str
    atp_id: str
    audit_record_id: str
    run_id: str
    graph_id: str
    graph_manifest_digest: str
    verification_verdict: str
    executor_invocation_count: int
    operation_count: int
    capture_mode: str
    viewer_url: str
    public_verify_url: str
    workspace: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ControlledLocalLedger:
    """Small SQLite fixture used by the public quickstart.

    The operation reference is a primary key. Replaying the same quickstart
    operation therefore cannot create a second local consequence. This is a
    controlled local demonstration only; it does not represent an external
    payment, email, purchase, or customer action.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS quickstart_operations (
                    operation_ref TEXT PRIMARY KEY,
                    result_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS quickstart_executor_invocations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    operation_ref TEXT NOT NULL,
                    invoked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def execute_once(self, *, operation_ref: str, result: dict[str, Any]) -> str:
        encoded = json.dumps(result, sort_keys=True, separators=(",", ":"))
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO quickstart_executor_invocations(operation_ref) VALUES (?)",
                (operation_ref,),
            )
            cursor = conn.execute(
                "INSERT OR IGNORE INTO quickstart_operations(operation_ref, result_json) VALUES (?, ?)",
                (operation_ref, encoded),
            )
            return "CREATED" if cursor.rowcount == 1 else "REPLAYED"

    def count(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) FROM quickstart_operations").fetchone()
            return int(row[0]) if row else 0

    def invocation_count(self, operation_ref: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM quickstart_executor_invocations WHERE operation_ref = ?",
                (operation_ref,),
            ).fetchone()
            return int(row[0]) if row else 0


def _json_body(response_body: bytes) -> dict[str, Any]:
    try:
        decoded = json.loads(response_body.decode("utf-8"))
    except Exception as exc:  # pragma: no cover - defensive boundary
        raise QuickstartError("AURORA returned a non-JSON response") from exc
    if not isinstance(decoded, dict):
        raise QuickstartError("AURORA returned an unexpected JSON response")
    return decoded


def _error_detail(payload: dict[str, Any]) -> str:
    detail = payload.get("detail")
    if isinstance(detail, str):
        return detail
    if isinstance(detail, dict):
        return str(detail.get("message") or detail.get("detail") or detail)
    error = payload.get("error")
    if isinstance(error, dict):
        return str(error.get("detail") or error.get("code") or error)
    return "request rejected"


def _sample_record_payload(operation_ref: str) -> dict[str, Any]:
    return {
        "decision_type": "AURORA self-serve quickstart",
        "applicant": "Controlled local ledger operation",
        "applicant_id": operation_ref,
        "amount_requested": None,
        "jurisdiction": "Other",
        "ai_system": "aurora-agent quickstart",
        "model_hash": QUICKSTART_RELEASE_ID,
        "ai_score": 100,
        "threshold": 50,
        "human_review": True,
        "reviewer_id": "quickstart:local-operator",
        "final_accountability": "Quickstart operator",
        "policy_ref": "AURORA-QUICKSTART-V0.1",
        "risk_flags": [],
        "override": False,
        "notes": (
            "Controlled self-serve sample. No external payment, message, purchase, "
            "or customer-side consequence is performed."
        ),
        "no_ai_mode": True,
        "outcome": "APPROVED",
        "score_direction": "SCORE_UPWARD_PASS",
    }


class QuickstartRunner:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = DEFAULT_API_BASE_URL,
        frontend_url: str = DEFAULT_FRONTEND_URL,
        workspace: str | Path = ".aurora-quickstart",
        transport=None,
        id_factory: Optional[Callable[[], str]] = None,
    ) -> None:
        if not api_key or not str(api_key).strip():
            raise ValueError("api_key must not be empty")
        self.api_key = str(api_key).strip()
        self.base_url = str(base_url).rstrip("/")
        self.frontend_url = str(frontend_url).rstrip("/")
        self.workspace_root = Path(workspace)
        self.transport = transport or UrllibTransport(
            base_url=self.base_url,
            api_key=self.api_key,
            timeout=45.0,
        )
        self.id_factory = id_factory or (lambda: uuid.uuid4().hex)

    def _create_sample_record(self, *, operation_ref: str) -> dict[str, Any]:
        body = json.dumps(
            _sample_record_payload(operation_ref),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        try:
            response = self.transport.request(
                method="POST",
                endpoint="/v1/audit-records",
                body=body,
                idempotency_key=(
                    f"aurora-agent.quickstart.audit-record:{operation_ref}"
                ),
            )
        except IngestionTransportError as exc:
            raise QuickstartError(f"Could not create the sample record: {exc}") from exc
        payload = _json_body(response.body)
        if response.status not in {200, 201}:
            raise QuickstartError(
                f"Sample record creation failed with HTTP {response.status}: "
                f"{_error_detail(payload)}"
            )
        atp_id = payload.get("atp_id") or payload.get("decision_id")
        record_id = payload.get("id") or payload.get("internal_decision_id")
        if not atp_id or not record_id:
            raise QuickstartError("Sample record response did not include record identifiers")
        return payload

    def run(self) -> QuickstartResult:
        suffix = self.id_factory()
        run_id = f"run_quickstart_{suffix}"
        operation_ref = f"op_quickstart_{suffix}"
        authorization_ref = f"auth_quickstart_{suffix}"
        outcome_ref = f"outcome_quickstart_{suffix}"
        workspace = self.workspace_root / run_id
        workspace.mkdir(parents=True, exist_ok=True)

        record = self._create_sample_record(operation_ref=operation_ref)
        atp_id = str(record.get("atp_id") or record.get("decision_id"))
        audit_record_id = str(record.get("id") or record.get("internal_decision_id"))

        client = IngestionClient(
            base_url=self.base_url,
            api_key=self.api_key,
            outbox_path=workspace / "aurora_ingestion_outbox.db",
            transport=self.transport,
        )
        run = client.start_run(
            atp_id=atp_id,
            capture_mode=CAPTURE_DIGEST_ONLY,
            runtime="aurora-agent-quickstart",
            release_id=QUICKSTART_RELEASE_ID,
            boundary_id="aurora-agent.quickstart.controlled-local-ledger",
            boundary_version="0.1",
            source="aurora-agent",
            run_id=run_id,
        )

        authorization_event = run.capture(
            "authorization",
            {
                "authorization_ref": authorization_ref,
                "scope": "controlled-local-ledger",
                "approved_operation_ref": operation_ref,
            },
            event_id=f"evt_{suffix}_authorization",
            actor="quickstart:local-operator",
            authorization_ref=authorization_ref,
            metadata={"quickstart": True, "controlled_fixture": True},
        )
        request_event = run.capture(
            "tool_request",
            {
                "tool_name": "quickstart_local_ledger.append",
                "operation_ref": operation_ref,
                "value": 1,
            },
            event_id=f"evt_{suffix}_tool_request",
            parent_event_ids=[authorization_event],
            authorization_ref=authorization_ref,
            operation_ref=operation_ref,
            metadata={"quickstart": True, "external_consequence": False},
        )
        execution_event = run.capture(
            "tool_execution",
            {
                "tool_name": "quickstart_local_ledger.append",
                "operation_ref": operation_ref,
                "execution_boundary": "local-sqlite-primary-key",
            },
            event_id=f"evt_{suffix}_tool_execution",
            parent_event_ids=[request_event],
            authorization_ref=authorization_ref,
            operation_ref=operation_ref,
            metadata={"commit_before_consequence": True},
        )

        ledger = ControlledLocalLedger(workspace / "controlled_action_ledger.db")
        ledger_status = ledger.execute_once(
            operation_ref=operation_ref,
            result={"status": "SUCCEEDED", "value": 1},
        )
        operation_count = ledger.count()
        executor_invocation_count = ledger.invocation_count(operation_ref)
        if (
            ledger_status != "CREATED"
            or operation_count != 1
            or executor_invocation_count != 1
        ):
            raise QuickstartError("Controlled action did not satisfy exactly-once acceptance")

        outcome_event = run.capture(
            "tool_outcome",
            {
                "operation_ref": operation_ref,
                "status": "SUCCEEDED",
                "ledger_status": ledger_status,
                "operation_count": operation_count,
            },
            event_id=f"evt_{suffix}_tool_outcome",
            parent_event_ids=[execution_event],
            operation_ref=operation_ref,
            outcome_ref=outcome_ref,
            metadata={"quickstart": True, "external_consequence": False},
        )
        final_event = run.capture(
            "final_decision",
            {
                "decision": "ACCEPTED",
                "operation_ref": operation_ref,
                "outcome_ref": outcome_ref,
            },
            event_id=f"evt_{suffix}_final_decision",
            parent_event_ids=[outcome_event],
            operation_ref=operation_ref,
            outcome_ref=outcome_ref,
            metadata={"quickstart": True},
        )
        run.finalize(root_event_id=final_event)

        flushed = client.flush()
        if not flushed or any(item.state != STATE_ACKNOWLEDGED for item in flushed):
            failed = next((item for item in flushed if item.state != STATE_ACKNOWLEDGED), None)
            code = failed.error_code if failed else "NO_ACKNOWLEDGEMENT"
            raise QuickstartError(
                f"Evidence delivery did not complete. Local outbox is preserved ({code}). "
                "Run `aurora-agent outbox flush --outbox <path>` to retry evidence only."
            )

        detail = client.read_run(run_id)
        verification = client.verify_run(run_id)
        graph_id = str(detail.get("graph_id") or "")
        graph_manifest_digest = str(detail.get("graph_manifest_digest") or "")
        verdict = str(verification.get("verdict") or "")
        if detail.get("state") != "FINALIZED" or not graph_id or verdict != "VALID":
            raise QuickstartError(
                "Quickstart evidence was delivered but final acceptance was not reached "
                f"(state={detail.get('state')}, verdict={verdict or 'UNKNOWN'})."
            )

        viewer_url = (
            f"{self.frontend_url}/verify-app/{quote(atp_id, safe='')}"
            f"?graph={quote(graph_id, safe='')}#compositional-evidence"
        )
        public_verify_url = f"{self.frontend_url}/verify/{quote(atp_id, safe='')}"
        result = QuickstartResult(
            acceptance="PASS",
            atp_id=atp_id,
            audit_record_id=audit_record_id,
            run_id=run_id,
            graph_id=graph_id,
            graph_manifest_digest=graph_manifest_digest,
            verification_verdict=verdict,
            executor_invocation_count=executor_invocation_count,
            operation_count=operation_count,
            capture_mode=CAPTURE_DIGEST_ONLY,
            viewer_url=viewer_url,
            public_verify_url=public_verify_url,
            workspace=str(workspace.resolve()),
        )
        (workspace / "quickstart_result.json").write_text(
            json.dumps(result.to_dict(), sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        return result