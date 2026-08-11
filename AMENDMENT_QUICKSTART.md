# Amendment / Correction quickstart

`aurora-agent` 0.8 adds a client for the AuroraSeal Amendment v1.0 lifecycle.
Existing sealed evidence remains immutable. A correction is represented by a
new complete successor record plus a separately sealed Amendment record.

## Client

```python
from aurora_agent import AmendmentClient

amendments = AmendmentClient(
    base_url="https://aurora-mvp-production.up.railway.app",
    api_key="<AURORA_API_KEY>",
)
```

## Read currentness before writing

```python
lifecycle = amendments.lifecycle("ase_...")
print(lifecycle["viewed_record"]["lifecycle_role"])
print(lifecycle["head"]["current_record_id"])
```

`VALID` and `CURRENT` are separate axes. A historical predecessor may remain
cryptographically valid after a later correction.

## Prepare a complete successor

Do not patch or silently copy predecessor fields. Supply a complete AI Output or
AI Decision creation request.

```python
prepared = amendments.prepare_ai_decision_successor(
    lifecycle["head"]["current_record_id"],
    successor_request,
    idempotency_key="correction-successor-2026-08-11",
)
successor_id = prepared["successor_record_id"]
```

The prepared successor is `PENDING_SUCCESSOR`. Seal it with the existing
`AIDecisionClient` or `AIOutputClient` before activating an amendment.

```python
from aurora_agent import AIDecisionClient

decisions = AIDecisionClient(
    base_url="https://aurora-mvp-production.up.railway.app",
    api_key="<AURORA_API_KEY>",
)
decisions.seal(successor_id, idempotency_key="seal-corrected-decision")
```

## Seal the Amendment against the observed head

```python
from aurora_agent import build_amendment_request_from_lifecycle

request = build_amendment_request_from_lifecycle(
    lifecycle,
    amendment_type="correction",
    successor_record_id=successor_id,
    reason_code="declared_outcome_corrected",
    reason="The previously declared outcome was corrected.",
    occurred_at="2026-08-11T13:00:00.000000Z",
    actor={
        "actor_type": "human",
        "actor_id": "operator-1",
        "actor_role": "organization_operator",
    },
    privacy={
        "contains_personal_data": False,
        "redaction_status": "not_applicable",
        "legal_hold_status": "not_applicable",
        "public_display_mode": "metadata_only",
    },
)

result = amendments.seal_amendment(
    request,
    idempotency_key="activate-correction-2026-08-11",
)
```

If another writer moved the chain head after `lifecycle()` was read, AURORA
returns HTTP 409 `AMENDMENT_CHAIN_HEAD_CONFLICT`. Reload the lifecycle; do not
rewrite the expected head client-side.

## Terminal events

`withdrawal` forbids a successor. `reversal` may omit a successor. A terminal
event can leave the chain with no current operational record.

## Download lifecycle evidence

```python
path = amendments.download_lifecycle_bundle("ase_...", ".")
print(path)
```

The Lifecycle Verification Bundle is a different format from the local
`aurora-agent` action bundle and from the standard record Evidence Bundle. It is 
designed for independent replay of the Amendment chain.

## Human Approval

Approval evidence is exact-digest bound. Approval of predecessor A remains
historically verifiable after A is corrected to B, but it does not approve B.
If B requires Human Approval, create a new Stage D process for B.

## Non-claims

AuroraSeal verifies recorded integrity and lifecycle linkage. It does not certify
that an amendment was factually correct, legally required, fair, compliant, or
authorized outside the identity and evidence represented by the sealed records.