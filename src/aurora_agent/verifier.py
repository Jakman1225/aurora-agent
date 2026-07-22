from __future__ import annotations

import copy
import hashlib
import io
import json
import re
import zipfile
from pathlib import Path
from typing import Any, Mapping, Optional

from .boundary import Boundary
from .canonical import canonical_bytes, commitment, digest_bytes, strict_json_loads
from .exceptions import BoundaryViolation, CanonicalizationError
from .model import Phase, VerificationReport, Verdict

_BUNDLE_SCHEMA = "aurora.agent-sdk-evidence-bundle.v0.1"
_REQUIRED = frozenset({"manifest.json", "action.json", "events.json", "NON_CLAIMS.txt"})
_MAX_ENTRY = 8 * 1024 * 1024
_MAX_TOTAL = 32 * 1024 * 1024
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


def _check(status: str, name: str, detail: str) -> dict[str, str]:
    return {"status": status, "name": name, "detail": detail}


def _report(verdict: Verdict, *, action_id: Optional[str] = None, proposal_digest: Optional[str] = None,
            terminal_phase: Optional[str] = None, outcome_strength: Optional[str] = None,
            checks: list[dict[str, str]] | None = None, errors: list[str] | None = None,
            warnings: list[str] | None = None) -> VerificationReport:
    return VerificationReport(
        verdict=verdict,
        action_id=action_id,
        proposal_digest=proposal_digest,
        terminal_phase=terminal_phase,
        outcome_strength=outcome_strength,
        checks=tuple(checks or []),
        errors=tuple(errors or []),
        warnings=tuple(warnings or []),
    )


def _read_bundle(path: str | Path) -> tuple[dict[str, bytes], Optional[VerificationReport]]:
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            seen: set[str] = set()
            total = 0
            for info in infos:
                name = info.filename
                normalized = name.replace("\\", "/")
                if normalized != name or name.startswith("/") or ".." in Path(name).parts:
                    return {}, _report(Verdict.INVALID, errors=[f"unsafe ZIP path: {name!r}"])
                folded = name.casefold()
                if folded in seen:
                    return {}, _report(Verdict.INVALID, errors=[f"duplicate/colliding ZIP entry: {name!r}"])
                seen.add(folded)
                mode = (info.external_attr >> 16) & 0o170000
                if mode == 0o120000:
                    return {}, _report(Verdict.INVALID, errors=[f"symlink ZIP entry: {name!r}"])
                if info.file_size > _MAX_ENTRY:
                    return {}, _report(Verdict.INVALID, errors=[f"oversized ZIP entry: {name!r}"])
                total += info.file_size
                if total > _MAX_TOTAL:
                    return {}, _report(Verdict.INVALID, errors=["bundle exceeds total size limit"])
            names = {info.filename for info in infos if not info.is_dir()}
            missing = sorted(_REQUIRED - names)
            if missing:
                return {}, _report(Verdict.INCOMPLETE, errors=[f"missing required artifact(s): {missing}"])
            unexpected = sorted(names - _REQUIRED)
            if unexpected:
                return {}, _report(Verdict.INVALID, errors=[f"undeclared artifact(s): {unexpected}"])
            return {name: archive.read(name) for name in names}, None
    except (OSError, zipfile.BadZipFile) as exc:
        return {}, _report(Verdict.INVALID, errors=[f"bundle could not be opened: {exc}"])


def verify_bundle(
    path: str | Path,
    *,
    supplied_arguments: Optional[Mapping[str, Any]] = None,
    supplied_result: Any = None,
    supplied_result_present: bool = False,
) -> VerificationReport:
    files, early = _read_bundle(path)
    if early is not None:
        return early
    checks: list[dict[str, str]] = []
    errors: list[str] = []
    warnings: list[str] = []

    try:
        manifest = strict_json_loads(files["manifest.json"], label="manifest.json")
        action = strict_json_loads(files["action.json"], label="action.json")
        events = strict_json_loads(files["events.json"], label="events.json")
    except CanonicalizationError as exc:
        return _report(Verdict.INVALID, errors=[str(exc)])

    if canonical_bytes(manifest) != files["manifest.json"]:
        errors.append("manifest.json is not exact canonical JSON")
    if canonical_bytes(action) != files["action.json"]:
        errors.append("action.json is not exact canonical JSON")
    if canonical_bytes(events) != files["events.json"]:
        errors.append("events.json is not exact canonical JSON")
    if errors:
        return _report(Verdict.INVALID, errors=errors)

    if not isinstance(manifest, dict) or manifest.get("schema_version") != _BUNDLE_SCHEMA:
        return _report(Verdict.UNSUPPORTED, errors=["unsupported bundle schema"])
    if manifest.get("sdk_version") != "0.1.0":
        return _report(Verdict.UNSUPPORTED, errors=["unsupported SDK bundle version"])
    if manifest.get("canonicalization_profile") != "aurora-agent-action-json" or manifest.get("canonicalization_version") != "0.1":
        return _report(Verdict.UNSUPPORTED, errors=["unsupported canonicalization profile"])
    if manifest.get("hash_algorithm") != "sha256":
        return _report(Verdict.UNSUPPORTED, errors=["unsupported hash algorithm"])

    declared = manifest.get("files")
    if not isinstance(declared, dict):
        return _report(Verdict.INVALID, errors=["manifest files must be an object"])
    for name in ("action.json", "events.json", "NON_CLAIMS.txt"):
        expected = declared.get(name)
        if not isinstance(expected, str) or not _SHA256_RE.fullmatch(expected):
            errors.append(f"invalid manifest digest for {name}")
        elif digest_bytes(files[name]) != expected:
            errors.append(f"digest mismatch for {name}")
    if errors:
        return _report(Verdict.INVALID, errors=errors)
    checks.append(_check("OK", "bundle_integrity", "declared artifact digests verified"))

    if not isinstance(action, dict) or not isinstance(events, list):
        return _report(Verdict.INVALID, errors=["action/events root type invalid"])
    action_id = action.get("action_id")
    proposal_digest = action.get("proposal_digest")
    if manifest.get("action_id") != action_id or manifest.get("proposal_digest") != proposal_digest:
        return _report(Verdict.INVALID, errors=["manifest identity contradicts action"])

    try:
        boundary = Boundary.from_dict(action["boundary"])
        subject = boundary.subject(tool=action["tool_name"], arguments=action["arguments"])
        recomputed = commitment(subject)
    except (KeyError, TypeError, ValueError, BoundaryViolation, CanonicalizationError) as exc:
        return _report(Verdict.INVALID, action_id=action_id, proposal_digest=proposal_digest, errors=[f"action boundary invalid: {exc}"])
    if recomputed != proposal_digest:
        return _report(Verdict.INVALID, action_id=action_id, proposal_digest=proposal_digest, errors=["proposal commitment mismatch"])
    checks.append(_check("OK", "proposal_commitment", "boundary projection and proposal digest verified"))

    if supplied_arguments is not None:
        try:
            supplied_digest = commitment(boundary.subject(tool=action["tool_name"], arguments=supplied_arguments))
        except Exception as exc:
            return _report(Verdict.INVALID, action_id=action_id, proposal_digest=proposal_digest, errors=[f"supplied arguments rejected: {exc}"])
        if supplied_digest != proposal_digest:
            return _report(Verdict.INVALID, action_id=action_id, proposal_digest=proposal_digest, errors=["supplied arguments do not match committed proposal"])
        checks.append(_check("OK", "supplied_arguments", "supplied arguments match committed proposal"))

    if not events:
        return _report(Verdict.INCOMPLETE, action_id=action_id, proposal_digest=proposal_digest, errors=["event chain is empty"])

    phases: list[str] = []
    previous_sequence = 0
    for event in events:
        if not isinstance(event, dict):
            return _report(Verdict.INVALID, action_id=action_id, proposal_digest=proposal_digest, errors=["event entry is not an object"])
        sequence = event.get("sequence")
        if not isinstance(sequence, int) or sequence <= previous_sequence:
            return _report(Verdict.INVALID, action_id=action_id, proposal_digest=proposal_digest, errors=["event sequence is not strictly increasing"])
        previous_sequence = sequence
        if event.get("action_id") != action_id:
            return _report(Verdict.INVALID, action_id=action_id, proposal_digest=proposal_digest, errors=["event action identity mismatch"])
        protected = {
            "event_id": event.get("event_id"),
            "action_id": event.get("action_id"),
            "phase": event.get("phase"),
            "created_at": event.get("created_at"),
            "body": event.get("body"),
        }
        if commitment(protected) != event.get("event_digest"):
            return _report(Verdict.INVALID, action_id=action_id, proposal_digest=proposal_digest, errors=[f"event digest mismatch at sequence {sequence}"])
        phases.append(str(event.get("phase")))

    if phases[0] != Phase.PROPOSED.value:
        return _report(Verdict.INVALID, action_id=action_id, proposal_digest=proposal_digest, errors=["event chain does not begin with PROPOSED"])
    if events[0]["body"].get("proposal_id") != action.get("proposal_id"):
        return _report(Verdict.INVALID, action_id=action_id, proposal_digest=proposal_digest, errors=["PROPOSED event proposal identity mismatch"])
    if len(phases) < 5:
        return _report(Verdict.INCOMPLETE, action_id=action_id, proposal_digest=proposal_digest, errors=["terminal lifecycle evidence is incomplete"])
    if phases[1] not in (Phase.AUTHORIZED.value, Phase.POLICY_PASSED.value):
        return _report(Verdict.INVALID, action_id=action_id, proposal_digest=proposal_digest, errors=["second event must be AUTHORIZED or POLICY_PASSED"])
    authorization_required = action.get("authorization_required")
    if type(authorization_required) is not bool:
        return _report(Verdict.INVALID, action_id=action_id, proposal_digest=proposal_digest, errors=["authorization_required must be boolean"])
    if authorization_required and phases[1] != Phase.AUTHORIZED.value:
        return _report(Verdict.INVALID, action_id=action_id, proposal_digest=proposal_digest, errors=["authorization-required action lacks AUTHORIZED gate"])
    if not authorization_required and phases[1] != Phase.POLICY_PASSED.value:
        return _report(Verdict.INVALID, action_id=action_id, proposal_digest=proposal_digest, errors=["policy-passed action has incompatible gate phase"])
    expected = [Phase.PROPOSED.value, phases[1], Phase.PRECOMMITTED.value, Phase.STARTED.value]
    if phases[:4] != expected:
        return _report(Verdict.INVALID, action_id=action_id, proposal_digest=proposal_digest, errors=[f"invalid lifecycle prefix: {phases[:4]}"])
    if len(phases) != 5 or phases[4] not in (Phase.SUCCEEDED.value, Phase.FAILED.value, Phase.UNKNOWN.value):
        return _report(Verdict.INVALID, action_id=action_id, proposal_digest=proposal_digest, errors=["exactly one supported terminal event is required"])

    for index in (0, 1, 2):
        if events[index]["body"].get("proposal_digest") != proposal_digest:
            return _report(Verdict.INVALID, action_id=action_id, proposal_digest=proposal_digest, errors=[f"proposal linkage mismatch at phase {phases[index]}"])
    gate_body = events[1]["body"]
    gate_reference = (
        gate_body.get("authorization_id")
        if phases[1] == Phase.AUTHORIZED.value
        else gate_body.get("policy_pass_id")
    )
    precommit_body = events[2]["body"]
    if precommit_body.get("gate_reference") != gate_reference:
        return _report(Verdict.INVALID, action_id=action_id, proposal_digest=proposal_digest, errors=["precommit gate reference mismatch"])
    if precommit_body.get("boundary_id") != action["boundary"].get("boundary_id") or precommit_body.get("boundary_version") != action["boundary"].get("version"):
        return _report(Verdict.INVALID, action_id=action_id, proposal_digest=proposal_digest, errors=["precommit boundary identity mismatch"])
    if precommit_body.get("capture_mode") != action["boundary"].get("capture_mode"):
        return _report(Verdict.INVALID, action_id=action_id, proposal_digest=proposal_digest, errors=["precommit capture mode mismatch"])
    if precommit_body.get("canonicalization_profile") != "aurora-agent-action-json" or precommit_body.get("canonicalization_version") != "0.1" or precommit_body.get("hash_algorithm") != "sha256":
        return _report(Verdict.INVALID, action_id=action_id, proposal_digest=proposal_digest, errors=["precommit canonicalization metadata mismatch"])
    precommit_id = precommit_body.get("commitment_id")
    if not isinstance(precommit_id, str) or precommit_id == "":
        return _report(Verdict.INVALID, action_id=action_id, proposal_digest=proposal_digest, errors=["precommit commitment identity missing"])
    if events[3]["body"].get("precommit_id") != precommit_id:
        return _report(Verdict.INVALID, action_id=action_id, proposal_digest=proposal_digest, errors=["STARTED does not link to precommit"])
    if events[2]["sequence"] >= events[3]["sequence"]:
        return _report(Verdict.INVALID, action_id=action_id, proposal_digest=proposal_digest, errors=["precommit does not precede STARTED"])
    checks.append(_check("OK", "lifecycle", "PROPOSED -> gate -> PRECOMMITTED -> STARTED -> terminal linkage verified"))
    checks.append(_check("OK", "precommit_before_start", "precommit sequence precedes execution STARTED"))

    terminal = events[4]
    terminal_phase = phases[4]
    body = terminal["body"]
    if body.get("precommit_id") != precommit_id:
        return _report(Verdict.INVALID, action_id=action_id, proposal_digest=proposal_digest, terminal_phase=terminal_phase, errors=["terminal event does not link to precommit"])
    outcome_strength = body.get("outcome_evidence_strength")
    if outcome_strength not in {"O0", "O1", "O2", "O3"}:
        return _report(Verdict.INVALID, action_id=action_id, proposal_digest=proposal_digest, terminal_phase=terminal_phase, errors=["invalid outcome evidence strength"])
    operation_reference = body.get("operation_reference")
    if operation_reference is not None and (not isinstance(operation_reference, str) or operation_reference == ""):
        return _report(Verdict.INVALID, action_id=action_id, proposal_digest=proposal_digest, terminal_phase=terminal_phase, errors=["operation_reference must be null or a non-empty string"])
    if outcome_strength in {"O1", "O2", "O3"} and operation_reference is None:
        return _report(Verdict.INVALID, action_id=action_id, proposal_digest=proposal_digest, terminal_phase=terminal_phase, errors=["O1-O3 require an operation_reference"])
    if terminal_phase == Phase.UNKNOWN.value and body.get("evidence_completeness") != "INCOMPLETE":
        return _report(Verdict.INVALID, action_id=action_id, proposal_digest=proposal_digest, terminal_phase=terminal_phase, errors=["UNKNOWN must remain INCOMPLETE"])
    if terminal_phase in (Phase.SUCCEEDED.value, Phase.FAILED.value) and body.get("evidence_completeness") != "COMPLETE":
        return _report(Verdict.INVALID, action_id=action_id, proposal_digest=proposal_digest, terminal_phase=terminal_phase, errors=["known terminal phase must be COMPLETE within local bundle scope"])

    if "result" in body:
        expected_result_digest = commitment({"commitment_subject": "execution_result_observation", "result": body["result"]})
        if body.get("result_digest") != expected_result_digest:
            return _report(Verdict.INVALID, action_id=action_id, proposal_digest=proposal_digest, terminal_phase=terminal_phase, errors=["result commitment mismatch"])
    elif body.get("result_digest") is not None:
        return _report(Verdict.INVALID, action_id=action_id, proposal_digest=proposal_digest, terminal_phase=terminal_phase, errors=["result digest exists without result observation"])

    if supplied_result_present:
        supplied_digest = commitment({"commitment_subject": "execution_result_observation", "result": supplied_result})
        if supplied_digest != body.get("result_digest"):
            return _report(Verdict.INVALID, action_id=action_id, proposal_digest=proposal_digest, terminal_phase=terminal_phase, outcome_strength=outcome_strength, errors=["supplied result does not match terminal result commitment"])
        checks.append(_check("OK", "supplied_result", "supplied result matches terminal commitment"))

    non_claims = files["NON_CLAIMS.txt"].decode("utf-8", errors="strict")
    required_phrases = (
        "does not prove capture completeness",
        "does not prove external anchoring",
        "does not prove qualified timestamp status",
    )
    missing_claims = [phrase for phrase in required_phrases if phrase not in non_claims]
    if missing_claims:
        return _report(Verdict.INCOMPLETE, action_id=action_id, proposal_digest=proposal_digest, terminal_phase=terminal_phase, outcome_strength=outcome_strength, errors=[f"mandatory non-claims missing: {missing_claims}"])
    checks.append(_check("OK", "non_claims", "mandatory limitations preserved"))

    warnings.append("VALID is limited to this local SDK evidence bundle and declared capture boundary.")
    return _report(
        Verdict.VALID,
        action_id=action_id,
        proposal_digest=proposal_digest,
        terminal_phase=terminal_phase,
        outcome_strength=outcome_strength,
        checks=checks,
        warnings=warnings,
    )


def format_report(report: VerificationReport) -> str:
    lines = [
        "AURORA AGENT EVIDENCE SDK VERIFICATION",
        f"Overall verdict     {report.verdict.value}",
        f"Action ID           {report.action_id or '-'}",
        f"Proposal digest     {report.proposal_digest or '-'}",
        f"Terminal phase      {report.terminal_phase or '-'}",
        f"Outcome strength    {report.outcome_strength or '-'}",
        "",
        "CHECKS",
    ]
    lines.extend(
        f"[{item['status']:<12}] {item['name']}: {item['detail']}" for item in report.checks
    )
    if report.errors:
        lines.extend(["", "ERRORS", *[f"- {item}" for item in report.errors]])
    if report.warnings:
        lines.extend(["", "WARNINGS", *[f"- {item}" for item in report.warnings]])
    return "\n".join(lines)
