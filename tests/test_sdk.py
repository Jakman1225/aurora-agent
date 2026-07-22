from __future__ import annotations

import hashlib
import json
import shutil
import zipfile
from pathlib import Path

import pytest

from aurora_agent import (
    Aurora,
    Boundary,
    BoundaryViolation,
    CanonicalizationError,
    FieldRule,
    LifecycleError,
    OutcomeEvidenceStrength,
    Verdict,
    canonical_bytes,
    commitment,
    verify_bundle,
)


class Deterministic:
    def __init__(self) -> None:
        self.n = 0

    def clock(self) -> str:
        self.n += 1
        return f"2026-07-14T00:00:{self.n:02d}.000000Z"

    def ident(self, prefix: str) -> str:
        self.n += 1
        return f"{prefix}_{self.n:032x}"


def payment_boundary() -> Boundary:
    return Boundary.strict(
        boundary_id="payment.execute",
        version="0.1",
        tool="send_payment",
        fields={
            "recipient": FieldRule.nonblank_string(),
            "amount": FieldRule.integer_exact(),
        },
        capture_mode="WRAPPED_EXECUTION",
    )


def client(tmp_path: Path) -> Aurora:
    deterministic = Deterministic()
    return Aurora.local(
        db_path=tmp_path / "sdk.db",
        boundaries=[payment_boundary()],
        clock=deterministic.clock,
        id_factory=deterministic.ident,
    )


def completed_action(tmp_path: Path, *, authorization_required: bool = True):
    sdk = client(tmp_path)
    action = sdk.propose(
        boundary="payment.execute",
        tool="send_payment",
        arguments={"recipient": "vendor_123", "amount": 25000},
        risk="high",
        authorization_required=authorization_required,
    )
    gate = (
        action.authorize(approved_by="finance_manager")
        if authorization_required
        else action.policy_pass(policy_id="payment-threshold-v1")
    )
    kwargs = {"authorization": gate} if authorization_required else {"policy_pass": gate}
    with action.execute(**kwargs) as execution:
        result = {"status": "completed", "operation_id": "pay_123"}
        execution.complete(result=result, operation_reference="pay_123")
    bundle = action.export(tmp_path / "action.zip")
    return sdk, action, bundle


def rewrite_zip(source: Path, target: Path, mutator) -> None:
    work = target.parent / (target.stem + "-work")
    if work.exists():
        shutil.rmtree(work)
    work.mkdir()
    with zipfile.ZipFile(source) as archive:
        archive.extractall(work)
    mutator(work)
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(work.iterdir()):
            archive.write(path, path.name)
    shutil.rmtree(work)


def refresh_manifest(work: Path, *names: str) -> None:
    manifest_path = work / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for name in names:
        manifest["files"][name] = "sha256:" + hashlib.sha256((work / name).read_bytes()).hexdigest()
    manifest_path.write_bytes(canonical_bytes(manifest))


def test_canonical_profile_is_deterministic_and_rejects_float():
    assert canonical_bytes({"b": 2, "a": 1}) == b'{"a":1,"b":2}'
    assert commitment({"a": 1}).startswith("sha256:")
    with pytest.raises(CanonicalizationError):
        canonical_bytes({"amount": 1.5})


def test_unicode_sequences_remain_distinct():
    assert commitment({"x": "é"}) != commitment({"x": "e\u0301"})


def test_boundary_is_exact_and_integral_float_normalizes():
    boundary = payment_boundary()
    assert boundary.normalize(
        tool="send_payment",
        arguments={"recipient": "vendor_123", "amount": 25000.0},
    ) == {"recipient": "vendor_123", "amount": 25000}
    with pytest.raises(BoundaryViolation):
        boundary.normalize(
            tool="send_payment",
            arguments={"recipient": "vendor_123", "amount": 25000, "memo": "x"},
        )
    with pytest.raises(BoundaryViolation):
        boundary.normalize(tool="send_payment", arguments={"recipient": " " , "amount": 1})


def test_authorized_success_path_and_bundle_verify(tmp_path: Path):
    sdk, action, bundle = completed_action(tmp_path)
    report = sdk.verify(bundle)
    assert report.verdict is Verdict.VALID
    assert report.terminal_phase == "SUCCEEDED"
    assert report.outcome_strength == "O0"
    phases = [event["phase"] for event in sdk._store.events(action.action_id)]
    assert phases == ["PROPOSED", "AUTHORIZED", "PRECOMMITTED", "STARTED", "SUCCEEDED"]


def test_policy_pass_success_path(tmp_path: Path):
    sdk, action, bundle = completed_action(tmp_path, authorization_required=False)
    assert sdk.verify(bundle).verdict is Verdict.VALID
    phases = [event["phase"] for event in sdk._store.events(action.action_id)]
    assert phases[1] == "POLICY_PASSED"


def test_execute_requires_exact_gate_and_replay_is_rejected(tmp_path: Path):
    sdk = client(tmp_path)
    action = sdk.propose(
        boundary="payment.execute",
        tool="send_payment",
        arguments={"recipient": "vendor_123", "amount": 25000},
        risk="high",
        authorization_required=True,
    )
    with pytest.raises(LifecycleError):
        action.execute()
    authorization = action.authorize(approved_by="finance_manager")
    execution = action.execute(authorization=authorization)
    with pytest.raises(LifecycleError):
        action.execute(authorization=authorization)
    with execution as entered:
        entered.complete(result={"status": "completed"})
    with pytest.raises(LifecycleError):
        entered.complete(result={"status": "again"})


def test_grant_from_other_action_is_rejected(tmp_path: Path):
    sdk = client(tmp_path)
    first = sdk.propose(boundary="payment.execute", tool="send_payment", arguments={"recipient": "a", "amount": 1}, risk="high", authorization_required=True)
    second = sdk.propose(boundary="payment.execute", tool="send_payment", arguments={"recipient": "b", "amount": 2}, risk="high", authorization_required=True)
    authorization = first.authorize(approved_by="manager")
    second.authorize(approved_by="manager")
    with pytest.raises(LifecycleError):
        second.execute(authorization=authorization)


def test_context_without_terminal_becomes_unknown(tmp_path: Path):
    sdk = client(tmp_path)
    action = sdk.propose(boundary="payment.execute", tool="send_payment", arguments={"recipient": "a", "amount": 1}, risk="medium")
    gate = action.policy_pass(policy_id="p1")
    with action.execute(policy_pass=gate):
        pass
    bundle = action.export(tmp_path / "unknown.zip")
    report = sdk.verify(bundle)
    assert report.verdict is Verdict.VALID
    assert report.terminal_phase == "UNKNOWN"


def test_exception_context_becomes_unknown_and_propagates(tmp_path: Path):
    sdk = client(tmp_path)
    action = sdk.propose(boundary="payment.execute", tool="send_payment", arguments={"recipient": "a", "amount": 1}, risk="medium")
    gate = action.policy_pass(policy_id="p1")
    with pytest.raises(RuntimeError, match="boom"):
        with action.execute(policy_pass=gate):
            raise RuntimeError("boom")
    assert sdk.verify(action.export(tmp_path / "exception.zip")).terminal_phase == "UNKNOWN"


def test_failed_path_is_valid_local_evidence(tmp_path: Path):
    sdk = client(tmp_path)
    action = sdk.propose(boundary="payment.execute", tool="send_payment", arguments={"recipient": "a", "amount": 1}, risk="medium")
    gate = action.policy_pass(policy_id="p1")
    with action.execute(policy_pass=gate) as execution:
        execution.fail(error={"code": "DECLINED"})
    report = sdk.verify(action.export(tmp_path / "failed.zip"))
    assert report.verdict is Verdict.VALID
    assert report.terminal_phase == "FAILED"


def test_export_is_deterministic(tmp_path: Path):
    _, action, first = completed_action(tmp_path)
    second = action.export(tmp_path / "action2.zip")
    assert first.read_bytes() == second.read_bytes()


def test_supplied_argument_tamper_is_invalid(tmp_path: Path):
    sdk, _, bundle = completed_action(tmp_path)
    report = sdk.verify(bundle, supplied_arguments={"recipient": "vendor_123", "amount": 2500})
    assert report.verdict is Verdict.INVALID


def test_supplied_result_tamper_is_invalid(tmp_path: Path):
    sdk, _, bundle = completed_action(tmp_path)
    report = sdk.verify(bundle, supplied_result={"status": "failed"}, supplied_result_present=True)
    assert report.verdict is Verdict.INVALID


def test_action_mutation_with_manifest_rewrite_is_invalid(tmp_path: Path):
    _, _, bundle = completed_action(tmp_path)
    tampered = tmp_path / "tampered.zip"

    def mutate(work: Path) -> None:
        action_path = work / "action.json"
        action = json.loads(action_path.read_text(encoding="utf-8"))
        action["arguments"]["amount"] = 2500
        action_path.write_bytes(canonical_bytes(action))
        refresh_manifest(work, "action.json")

    rewrite_zip(bundle, tampered, mutate)
    assert verify_bundle(tampered).verdict is Verdict.INVALID


def test_event_mutation_with_manifest_rewrite_is_invalid(tmp_path: Path):
    _, _, bundle = completed_action(tmp_path)
    tampered = tmp_path / "event-tampered.zip"

    def mutate(work: Path) -> None:
        events_path = work / "events.json"
        events = json.loads(events_path.read_text(encoding="utf-8"))
        events[2]["body"]["proposal_digest"] = "sha256:" + "0" * 64
        events_path.write_bytes(canonical_bytes(events))
        refresh_manifest(work, "events.json")

    rewrite_zip(bundle, tampered, mutate)
    assert verify_bundle(tampered).verdict is Verdict.INVALID


def test_missing_required_file_is_incomplete(tmp_path: Path):
    _, _, bundle = completed_action(tmp_path)
    incomplete = tmp_path / "incomplete.zip"

    def mutate(work: Path) -> None:
        (work / "events.json").unlink()

    rewrite_zip(bundle, incomplete, mutate)
    assert verify_bundle(incomplete).verdict is Verdict.INCOMPLETE


def test_unknown_schema_is_unsupported(tmp_path: Path):
    _, _, bundle = completed_action(tmp_path)
    unsupported = tmp_path / "unsupported.zip"

    def mutate(work: Path) -> None:
        path = work / "manifest.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["schema_version"] = "future.v9"
        path.write_bytes(canonical_bytes(manifest))

    rewrite_zip(bundle, unsupported, mutate)
    assert verify_bundle(unsupported).verdict is Verdict.UNSUPPORTED


def test_non_claims_removal_is_incomplete(tmp_path: Path):
    _, _, bundle = completed_action(tmp_path)
    incomplete = tmp_path / "claims.zip"

    def mutate(work: Path) -> None:
        path = work / "NON_CLAIMS.txt"
        path.write_text("nothing\n", encoding="utf-8")
        refresh_manifest(work, "NON_CLAIMS.txt")

    rewrite_zip(bundle, incomplete, mutate)
    assert verify_bundle(incomplete).verdict is Verdict.INCOMPLETE


def test_o1_requires_operation_reference_at_capture_time(tmp_path: Path):
    sdk = client(tmp_path)
    action = sdk.propose(
        boundary="payment.execute",
        tool="send_payment",
        arguments={"recipient": "a", "amount": 1},
        risk="medium",
    )
    gate = action.policy_pass(policy_id="p1")
    with pytest.raises(ValueError, match="operation_reference"):
        with action.execute(policy_pass=gate) as execution:
            execution.complete(
                result={"status": "completed"},
                outcome_evidence_strength=OutcomeEvidenceStrength.O1,
            )
    # The context manager preserves ambiguity rather than fabricating failure.
    report = sdk.verify(action.export(tmp_path / "o1-unknown.zip"))
    assert report.verdict is Verdict.VALID
    assert report.terminal_phase == "UNKNOWN"


def test_precommit_gate_link_mutation_is_invalid(tmp_path: Path):
    _, _, bundle = completed_action(tmp_path)
    tampered = tmp_path / "gate-link.zip"

    def mutate(work: Path) -> None:
        events_path = work / "events.json"
        events = json.loads(events_path.read_text(encoding="utf-8"))
        events[2]["body"]["gate_reference"] = "auth_wrong"
        protected = {
            "event_id": events[2]["event_id"],
            "action_id": events[2]["action_id"],
            "phase": events[2]["phase"],
            "created_at": events[2]["created_at"],
            "body": events[2]["body"],
        }
        events[2]["event_digest"] = commitment(protected)
        events_path.write_bytes(canonical_bytes(events))
        refresh_manifest(work, "events.json")

    rewrite_zip(bundle, tampered, mutate)
    assert verify_bundle(tampered).verdict is Verdict.INVALID


def test_started_precommit_link_mutation_is_invalid(tmp_path: Path):
    _, _, bundle = completed_action(tmp_path)
    tampered = tmp_path / "started-link.zip"

    def mutate(work: Path) -> None:
        events_path = work / "events.json"
        events = json.loads(events_path.read_text(encoding="utf-8"))
        events[3]["body"]["precommit_id"] = "cmt_wrong"
        protected = {
            "event_id": events[3]["event_id"],
            "action_id": events[3]["action_id"],
            "phase": events[3]["phase"],
            "created_at": events[3]["created_at"],
            "body": events[3]["body"],
        }
        events[3]["event_digest"] = commitment(protected)
        events_path.write_bytes(canonical_bytes(events))
        refresh_manifest(work, "events.json")

    rewrite_zip(bundle, tampered, mutate)
    assert verify_bundle(tampered).verdict is Verdict.INVALID
