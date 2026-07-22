from __future__ import annotations

import json
import os
import tempfile
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from .boundary import Boundary
from .canonical import CANONICALIZATION_PROFILE, CANONICALIZATION_VERSION, HASH_ALGORITHM, canonical_bytes, commitment, digest_bytes
from .exceptions import LifecycleError
from .model import Authorization, OutcomeEvidenceStrength, Phase, PolicyPass, VerificationReport
from .store import Store
from .verifier import verify_bundle

_BUNDLE_SCHEMA = "aurora.agent-sdk-evidence-bundle.v0.1"
_NON_CLAIMS = (
    "AURORA AGENT EVIDENCE SDK v0.1 NON-CLAIMS\n"
    "- VALID does not prove capture completeness or absence of bypass paths.\n"
    "- VALID does not prove external anchoring.\n"
    "- VALID does not prove certificate-path trust.\n"
    "- VALID does not prove qualified timestamp status.\n"
    "- VALID does not prove legal authorization, physical-world truth, causality, liability, or admissibility.\n"
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _default_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _zip_write(archive: zipfile.ZipFile, name: str, data: bytes) -> None:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o100644 << 16
    archive.writestr(info, data)


class Aurora:
    def __init__(
        self,
        *,
        db_path: str | Path,
        boundaries: list[Boundary],
        clock: Callable[[], str] = _utc_now,
        id_factory: Callable[[str], str] = _default_id,
    ) -> None:
        if not boundaries:
            raise ValueError("at least one boundary is required")
        self._boundaries = {item.boundary_id: item for item in boundaries}
        if len(self._boundaries) != len(boundaries):
            raise ValueError("boundary_id values must be unique")
        self._clock = clock
        self._id_factory = id_factory
        self._store = Store(db_path)

    @classmethod
    def local(
        cls,
        *,
        db_path: str | Path = "aurora_agent.db",
        boundaries: list[Boundary],
        clock: Callable[[], str] = _utc_now,
        id_factory: Callable[[str], str] = _default_id,
    ) -> "Aurora":
        return cls(db_path=db_path, boundaries=boundaries, clock=clock, id_factory=id_factory)

    @property
    def db_path(self) -> Path:
        return self._store.path

    def propose(
        self,
        *,
        boundary: str,
        tool: str,
        arguments: Mapping[str, Any],
        risk: str,
        authorization_required: bool = False,
        run_id: Optional[str] = None,
    ) -> "Action":
        if boundary not in self._boundaries:
            raise ValueError(f"unknown boundary: {boundary}")
        if not isinstance(risk, str) or risk == "":
            raise ValueError("risk must be a non-empty string")
        if type(authorization_required) is not bool:
            raise TypeError("authorization_required must be bool")
        selected = self._boundaries[boundary]
        normalized = selected.normalize(tool=tool, arguments=arguments)
        subject = selected.subject(tool=tool, arguments=normalized)
        proposal_digest = commitment(subject)
        created_at = self._clock()
        action_id = self._id_factory("act")
        proposal_id = self._id_factory("prop")
        resolved_run_id = run_id or self._id_factory("run")
        record = {
            "schema_version": "aurora.agent-action.v0.1",
            "action_id": action_id,
            "proposal_id": proposal_id,
            "run_id": resolved_run_id,
            "boundary": selected.to_dict(),
            "tool_name": tool,
            "arguments": normalized,
            "risk": str(risk),
            "authorization_required": bool(authorization_required),
            "proposal_digest": proposal_digest,
            "created_at": created_at,
        }
        event = self._event(
            action_id=action_id,
            phase=Phase.PROPOSED,
            body={"proposal_id": proposal_id, "proposal_digest": proposal_digest},
        )
        self._store.create_action(record, event)
        return Action(self, action_id)

    def _event(self, *, action_id: str, phase: Phase, body: dict[str, Any]) -> dict[str, Any]:
        return {
            "event_id": self._id_factory("evt"),
            "action_id": action_id,
            "phase": phase.value,
            "created_at": self._clock(),
            "body": body,
        }

    def action(self, action_id: str) -> "Action":
        self._store.action(action_id)
        return Action(self, action_id)

    def verify(
        self,
        bundle_path: str | Path,
        *,
        supplied_arguments: Optional[Mapping[str, Any]] = None,
        supplied_result: Any = None,
        supplied_result_present: bool = False,
    ) -> VerificationReport:
        return verify_bundle(
            bundle_path,
            supplied_arguments=supplied_arguments,
            supplied_result=supplied_result,
            supplied_result_present=supplied_result_present,
        )


class Action:
    def __init__(self, client: Aurora, action_id: str) -> None:
        self._client = client
        self.action_id = action_id

    @property
    def record(self) -> dict[str, Any]:
        return self._client._store.action(self.action_id)

    @property
    def proposal_digest(self) -> str:
        return str(self.record["proposal_digest"])

    def authorize(self, *, approved_by: str, method: str = "human") -> Authorization:
        if not isinstance(approved_by, str) or approved_by == "":
            raise ValueError("approved_by must be a non-empty string")
        if not isinstance(method, str) or method == "":
            raise ValueError("method must be a non-empty string")
        record = self.record
        if not record["authorization_required"]:
            raise LifecycleError("this proposal does not require authorization; use policy_pass")
        created_at = self._client._clock()
        grant_id = self._client._id_factory("auth")
        grant = {
            "grant_id": grant_id,
            "action_id": self.action_id,
            "grant_type": Phase.AUTHORIZED.value,
            "proposal_digest": record["proposal_digest"],
            "actor_ref": approved_by,
            "method": method,
            "decision_reference": None,
            "created_at": created_at,
        }
        event = self._client._event(
            action_id=self.action_id,
            phase=Phase.AUTHORIZED,
            body={
                "proposal_digest": record["proposal_digest"],
                "authorization_id": grant_id,
                "approved_by": approved_by,
                "method": method,
            },
        )
        self._client._store.create_grant(grant, event, expected_state=Phase.PROPOSED.value)
        return Authorization(grant_id, self.action_id, record["proposal_digest"], approved_by, method, created_at)

    def policy_pass(self, *, policy_id: str, decision_reference: Optional[str] = None) -> PolicyPass:
        if not isinstance(policy_id, str) or policy_id == "":
            raise ValueError("policy_id must be a non-empty string")
        if decision_reference is not None and (not isinstance(decision_reference, str) or decision_reference == ""):
            raise ValueError("decision_reference must be None or a non-empty string")
        record = self.record
        if record["authorization_required"]:
            raise LifecycleError("this proposal requires explicit authorization")
        created_at = self._client._clock()
        grant_id = self._client._id_factory("pol")
        grant = {
            "grant_id": grant_id,
            "action_id": self.action_id,
            "grant_type": Phase.POLICY_PASSED.value,
            "proposal_digest": record["proposal_digest"],
            "actor_ref": policy_id,
            "method": None,
            "decision_reference": decision_reference,
            "created_at": created_at,
        }
        event = self._client._event(
            action_id=self.action_id,
            phase=Phase.POLICY_PASSED,
            body={
                "proposal_digest": record["proposal_digest"],
                "policy_pass_id": grant_id,
                "policy_id": policy_id,
                "decision_reference": decision_reference,
            },
        )
        self._client._store.create_grant(grant, event, expected_state=Phase.PROPOSED.value)
        return PolicyPass(grant_id, self.action_id, record["proposal_digest"], policy_id, decision_reference, created_at)

    def execute(
        self,
        *,
        authorization: Optional[Authorization] = None,
        policy_pass: Optional[PolicyPass] = None,
    ) -> "Execution":
        if (authorization is None) == (policy_pass is None):
            raise LifecycleError("provide exactly one of authorization or policy_pass")
        record = self.record
        grant = authorization if authorization is not None else policy_pass
        assert grant is not None
        expected_phase = Phase.AUTHORIZED.value if authorization is not None else Phase.POLICY_PASSED.value
        if grant.action_id != self.action_id or grant.proposal_digest != record["proposal_digest"]:
            raise LifecycleError("gate grant does not bind this exact action proposal")
        precommit_id = self._client._id_factory("cmt")
        created_at = self._client._clock()
        event = self._client._event(
            action_id=self.action_id,
            phase=Phase.PRECOMMITTED,
            body={
                "proposal_digest": record["proposal_digest"],
                "commitment_id": precommit_id,
                "commitment_subject": "tool_invocation_request",
                "boundary_id": record["boundary"]["boundary_id"],
                "boundary_version": record["boundary"]["version"],
                "canonicalization_profile": CANONICALIZATION_PROFILE,
                "canonicalization_version": CANONICALIZATION_VERSION,
                "hash_algorithm": HASH_ALGORITHM,
                "capture_mode": record["boundary"]["capture_mode"],
                "anchor_state": "LOCAL",
                "gate_reference": grant.authorization_id if isinstance(grant, Authorization) else grant.policy_pass_id,
            },
        )
        grant_id = grant.authorization_id if isinstance(grant, Authorization) else grant.policy_pass_id
        self._client._store.precommit(
            action_id=self.action_id,
            grant_id=grant_id,
            expected_phase=expected_phase,
            event=event,
            consumed_at=created_at,
        )
        return Execution(self._client, self.action_id, precommit_id)

    def export(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        record = dict(self.record)
        record.pop("state", None)
        events = self._client._store.events(self.action_id)
        action_bytes = canonical_bytes(record)
        events_bytes = canonical_bytes(events)
        claims_bytes = _NON_CLAIMS.encode("utf-8")
        manifest = {
            "schema_version": _BUNDLE_SCHEMA,
            "sdk_version": "0.1.0",
            "action_id": record["action_id"],
            "proposal_digest": record["proposal_digest"],
            "canonicalization_profile": CANONICALIZATION_PROFILE,
            "canonicalization_version": CANONICALIZATION_VERSION,
            "hash_algorithm": HASH_ALGORITHM,
            "files": {
                "NON_CLAIMS.txt": digest_bytes(claims_bytes),
                "action.json": digest_bytes(action_bytes),
                "events.json": digest_bytes(events_bytes),
            },
        }
        manifest_bytes = canonical_bytes(manifest)
        temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        try:
            with zipfile.ZipFile(temporary, "w") as archive:
                for name, data in sorted(
                    {
                        "manifest.json": manifest_bytes,
                        "action.json": action_bytes,
                        "events.json": events_bytes,
                        "NON_CLAIMS.txt": claims_bytes,
                    }.items()
                ):
                    _zip_write(archive, name, data)
            os.replace(temporary, target)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
        return target


class Execution:
    def __init__(self, client: Aurora, action_id: str, precommit_id: str) -> None:
        self._client = client
        self.action_id = action_id
        self.precommit_id = precommit_id
        self._entered = False
        self._terminal = False

    def __enter__(self) -> "Execution":
        if self._entered:
            raise LifecycleError("execution context replay rejected")
        event = self._client._event(
            action_id=self.action_id,
            phase=Phase.STARTED,
            body={"precommit_id": self.precommit_id},
        )
        self._client._store.transition(
            action_id=self.action_id,
            expected_state=Phase.PRECOMMITTED.value,
            event=event,
        )
        self._entered = True
        return self

    def __exit__(self, exc_type, exc, traceback) -> bool:
        del traceback
        if not self._terminal:
            observation = (
                {"exception_type": exc_type.__name__, "message": str(exc)}
                if exc_type is not None
                else {"reason": "context_exited_without_terminal"}
            )
            self.unknown(observation=observation)
        return False

    def _terminal_event(
        self,
        *,
        phase: Phase,
        result: Any,
        result_present: bool,
        operation_reference: Optional[str],
        outcome_evidence_strength: OutcomeEvidenceStrength,
    ) -> None:
        if not self._entered:
            raise LifecycleError("execution context must be entered before terminal evidence")
        if self._terminal:
            raise LifecycleError("terminal replay rejected")
        if operation_reference is not None and (not isinstance(operation_reference, str) or operation_reference == ""):
            raise ValueError("operation_reference must be None or a non-empty string")
        if not isinstance(outcome_evidence_strength, OutcomeEvidenceStrength):
            raise TypeError("outcome_evidence_strength must be OutcomeEvidenceStrength")
        if outcome_evidence_strength in (
            OutcomeEvidenceStrength.O1,
            OutcomeEvidenceStrength.O2,
            OutcomeEvidenceStrength.O3,
        ) and operation_reference is None:
            raise ValueError("O1-O3 outcome evidence requires operation_reference")
        completeness = "INCOMPLETE" if phase is Phase.UNKNOWN else "COMPLETE"
        body: dict[str, Any] = {
            "precommit_id": self.precommit_id,
            "operation_reference": operation_reference,
            "outcome_evidence_strength": outcome_evidence_strength.value,
            "evidence_completeness": completeness,
        }
        if result_present:
            body["result"] = result
            body["result_digest"] = commitment(
                {"commitment_subject": "execution_result_observation", "result": result}
            )
        else:
            body["result_digest"] = None
        event = self._client._event(action_id=self.action_id, phase=phase, body=body)
        self._client._store.transition(
            action_id=self.action_id,
            expected_state=Phase.STARTED.value,
            event=event,
        )
        self._terminal = True

    def complete(
        self,
        *,
        result: Any,
        operation_reference: Optional[str] = None,
        outcome_evidence_strength: OutcomeEvidenceStrength = OutcomeEvidenceStrength.O0,
    ) -> None:
        self._terminal_event(
            phase=Phase.SUCCEEDED,
            result=result,
            result_present=True,
            operation_reference=operation_reference,
            outcome_evidence_strength=outcome_evidence_strength,
        )

    def fail(
        self,
        *,
        error: Any,
        operation_reference: Optional[str] = None,
        outcome_evidence_strength: OutcomeEvidenceStrength = OutcomeEvidenceStrength.O0,
    ) -> None:
        self._terminal_event(
            phase=Phase.FAILED,
            result={"error": error},
            result_present=True,
            operation_reference=operation_reference,
            outcome_evidence_strength=outcome_evidence_strength,
        )

    def unknown(
        self,
        *,
        observation: Any = None,
        operation_reference: Optional[str] = None,
        outcome_evidence_strength: OutcomeEvidenceStrength = OutcomeEvidenceStrength.O0,
    ) -> None:
        self._terminal_event(
            phase=Phase.UNKNOWN,
            result=observation,
            result_present=True,
            operation_reference=operation_reference,
            outcome_evidence_strength=outcome_evidence_strength,
        )
