# Human Approval quickstart

`aurora-agent` 0.7 adds a programmatic client for the AuroraSeal Human Approval
v1.0 contract. The client consumes the same D-1 through D-5 server surfaces
used by AuroraSeal production: policy-bound requirements, immutable review
events, deterministic process projections, reviewer eligibility, public
verification, and verification bundles.

Human Approval does not run approvals locally. AURORA remains authoritative for
policy binding, reviewer identity binding, process evaluation, signing, RFC 3161
timestamping, persistence, and idempotency.

## Authentication boundary

Two credentials have different jobs:

- `api_key`: identifies and scopes the AURORA organization for programmatic API
  access.
- `reviewer_token`: a Supabase access token for the human who is checking
  eligibility or writing a review event.

The SDK sends the reviewer token only in `X-Reviewer-Authorization` on
human-bound operations. It is retained only in memory by the client. Do not
persist it in source code, logs, evidence payloads, or the ingestion outbox.

```python
from aurora_agent import HumanApprovalClient

client = HumanApprovalClient(
    base_url="https://aurora-mvp-production.up.railway.app",
    api_key="<AURORA_API_KEY>",
    reviewer_token="<SUPABASE_ACCESS_TOKEN>",
)
```

## Read a policy gate and approval process

```python
record_id = "ase_..."
process_id = "release-approval-2026-08-10"

gate = client.gate(record_id, approval_process_id=process_id)
process = client.process(record_id, process_id)

print(gate["gate_status"])
print(process["process_status"])
print(process["counted_approver_count"])
print(process["remaining_approver_count"])
```

The process ID is a caller-chosen coordination identifier. Reusing an existing
process ID does not bypass AURORA's exact subject, tenant, policy-requirement,
sequence, or reviewer-identity checks.

## Register an immutable policy requirement

Create the exact frozen v1 requirement shape first. Set-valued reviewer roles
must be unique and lexicographically ascending.

```python
from aurora_agent import (
    build_approval_requirement,
    build_policy_requirement_binding,
)

requirement = build_approval_requirement(
    approval_required=True,
    required_review_level="level_2",
    required_reviewer_roles=["admin", "owner"],
    minimum_approver_count=2,
    separation_of_duties=True,
    escalation_required=False,
)

binding_request = build_policy_requirement_binding(
    policy_id="release-gate",
    policy_version="1.0",
    policy_digest="sha256:" + "a" * 64,
    requirement_snapshot=requirement,
)

binding = client.register_policy_requirement(
    binding_request,
    idempotency_key="release-gate-binding-2026-08-10",
)
```

A successful binding is immutable. The SDK does not merge multiple policy
requirements or weaken a server-bound requirement.

## Use reviewer eligibility as the approval template

Do not guess `event_sequence`, `declared_resulting_state`, or the requirement
snapshot. Ask AURORA for reviewer eligibility, then submit the exact
`approval_submission` returned by the server.

```python
eligibility = client.eligibility(record_id, process_id)

if eligibility["eligible_to_count"]:
    result = client.approve_from_eligibility(
        record_id,
        eligibility,
        reason_code="policy_requirement_met",
        reason="Reviewed the declared decision evidence and policy requirement.",
        idempotency_key="approval-owner-2026-08-10",
    )
    print(result["event"]["review"]["declared_resulting_state"])
else:
    print(eligibility["ineligibility_reasons"])
```

For a two-party process, an intermediate approval can produce
`second_reviewer_required`; the final distinct counting reviewer can produce
`multi_party_approved`. The SDK deliberately preserves the state returned by
AURORA rather than predicting it locally.

`execution_authorization_granted` defaults to `False` in
`approve_from_eligibility()`. Set it to `True` only when the reviewer is
actually granting execution authorization and the server permits that role to
do so.

## Other review events

The lower-level client exposes the existing server write surfaces:

```python
client.review(record_id, request)
client.approve(record_id, request)
client.reject(record_id, request)
client.override(record_id, request)
client.escalate(record_id, request)
client.defer(record_id, request)
```

These methods do not infer or repair request payloads. AURORA revalidates the
human identity, role, review level, exact requirement snapshot, sequence,
policy binding, declared resulting state, and event semantics before sealing
and persistence.

## Verification and bundles

Human Approval evidence attached to an AI Decision is included by the AURORA
AI Decision verification bundle when present. Use `AIDecisionClient` to obtain
that hosted artifact:

```python
from aurora_agent import AIDecisionClient

decisions = AIDecisionClient(
    base_url="https://aurora-mvp-production.up.railway.app",
    api_key="<AURORA_API_KEY>",
)

decisions.download_bundle(record_id, ".")
```

The `aurora-agent` local action-bundle verifier is a different format and does
not replace the standalone AuroraSeal Evidence Verification Bundle verifier.
Public AuroraSeal verification redacts authentication subjects, email
addresses, role-assignment references, identity evidence, signature values,
timestamp tokens, and certificate chains while preserving pseudonymous digest,
role, quorum, state, and integrity metadata.

## Non-claims

A valid Human Approval process proves only the integrity and evaluated state of
the recorded approval evidence under the bound requirement. It does not prove
that the decision is correct, lawful, fair, compliant, that a declared policy
is appropriate, or that a reviewer had legal authority outside the identity
and role evidence recorded by AURORA.