"""Client-side single-flight guard plus the authoritative isolated mTLS gate.

The journal is an additional fail-closed guard, never an execution permit.
Only the Executor may validate/consume authority or access provider credentials.
"""
import sqlite3
from pathlib import Path

from .client import json_response
from .contract import Artifacts, canonical, raw_digest, reference, validate
from .errors import ProtocolError, UncertainExecution
from .transport import HTTPS

class ExecutorClient:
    def __init__(self, base_url, *, ca_file, cert_file, key_file, transport=None):
        self.transport = transport or HTTPS(base_url, ca_file=ca_file, cert_file=cert_file, key_file=key_file)

    def _call(self, path, payload):
        return json_response(self.transport.request("POST", path, payload=payload))

    def issue(self, artifacts, raw_objects, *, binding_ref, snapshot_ref, provider_request_ref, authorization_id, expires_at):
        result = self._call("/issue", dict(artifacts=artifacts, raw_objects=raw_objects,
            binding_ref=binding_ref, snapshot_ref=snapshot_ref, provider_request_ref=provider_request_ref,
            authorization_id=authorization_id, expires_at=expires_at))
        validate(result["artifact"], "authorization"); validate(result["signature"], "signature")
        return result

    def status(self, attempt_id):
        return self._call("/status", dict(attempt_id=attempt_id))

    def observations(self, attempt_id, *, after=0):
        if type(after) is not int or after < 0:
            raise ProtocolError("CURSOR_INVALID")
        return self._call("/observations", dict(attempt_id=attempt_id, after=after))

class AttemptJournal:
    def __init__(self, path):
        if str(path) == ":memory:":
            raise ProtocolError("DURABLE_JOURNAL_REQUIRED")
        self.path = str(Path(path).resolve())
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS stage_g_attempts (tenant TEXT, gate TEXT, attempt TEXT, digest TEXT NOT NULL, phase TEXT NOT NULL, PRIMARY KEY(tenant,gate,attempt))")

    def claim(self, tenant, gate, attempt, digest):
        try:
            with sqlite3.connect(self.path) as db:
                db.execute("INSERT INTO stage_g_attempts VALUES (?,?,?,?,?)", (tenant, gate, attempt, digest, "CLAIMED"))
        except sqlite3.IntegrityError as exc:
            raise UncertainExecution("ATTEMPT_ALREADY_CLAIMED") from exc

    def finish(self, tenant, gate, attempt, phase):
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE stage_g_attempts SET phase=? WHERE tenant=? AND gate=? AND attempt=?", (phase, tenant, gate, attempt))

class GateAdapter:
    def __init__(self, executor, *, journal_path, tenant_id, gate_id):
        self.executor, self.tenant_id, self.gate_id = executor, tenant_id, gate_id
        self.journal = AttemptJournal(journal_path)

    def execute(self, envelope):
        """Exactly one SDK initiation; never automatically replay consume/dispatch.

        Pass the sealed, signed G-1 envelope. No callable provider fallback exists.
        A failed or lost response is resolved through status/observations only.
        """
        index = Artifacts(envelope["artifacts"], self.tenant_id)
        refs = envelope["references"]
        commitment = index.resolve(refs["commitment_ref"], "commitment")
        receipt = index.resolve(refs["receipt_ref"], "seal_receipt")
        auth = index.resolve(refs["authorization_ref"], "authorization")
        if receipt["body"]["commitment_ref"] != reference(commitment) or auth["body"]["audience_gate_id"] != self.gate_id:
            raise ProtocolError("ENVELOPE_SCOPE_MISMATCH")
        attempt = commitment["body"]["attempt_id"]
        self.journal.claim(self.tenant_id, self.gate_id, attempt, raw_digest(canonical(envelope)))
        try:
            consumption = self.executor._call("/consume", envelope)
            validate(consumption["artifact"], "gate_consumption")
            if consumption["artifact"]["tenant_id"] != self.tenant_id or consumption["artifact"]["body"]["attempt_id"] != attempt:
                raise ProtocolError("CONSUMPTION_SCOPE_MISMATCH")
            # No archive calls or interactive work between consumption and dispatch.
            dispatch = self.executor._call("/dispatch", dict(attempt_id=attempt))
            self.journal.finish(self.tenant_id, self.gate_id, attempt, "RESPONSE_RECEIVED")
            if dispatch.get("state") in {"UNKNOWN", "DISPATCH_CLAIMED"}:
                raise UncertainExecution()
            return dict(attempt_id=attempt, consumption=consumption, dispatch=dispatch)
        except BaseException:
            # Persisted claim survives interpreter death. Never clear it on failure.
            self.journal.finish(self.tenant_id, self.gate_id, attempt, "QUERY_ONLY")
            raise

    def status(self, attempt_id):
        return self.executor.status(attempt_id)

    def observations(self, attempt_id, *, after=0):
        return self.executor.observations(attempt_id, after=after)
