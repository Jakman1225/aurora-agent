# AURORA Agent Evidence SDK v0.2 — API Reference

Package: `aurora-agent`
Import: `aurora_agent`
Version: `0.3.1`
Python: `>=3.11,<3.14`

## Public construction surface

### `Boundary.strict(...)`

Declares one supported consequential-action boundary.

Required arguments:

- `boundary_id`: stable boundary identity.
- `version`: boundary contract version.
- `tool`: exact tool name permitted through the boundary.
- `fields`: mapping of argument names to `FieldRule` values.
- `capture_mode`: declared observation mode. Defaults to `SDK_SELF_REPORT`.

Strict boundaries reject undeclared argument keys.

### `FieldRule`

Supported field rules:

- `FieldRule.json()`
- `FieldRule.string()`
- `FieldRule.nonblank_string()`
- `FieldRule.integer()`
- `FieldRule.integer_exact()`
- `FieldRule.boolean()`

`integer_exact` accepts integers and finite integral floats, then normalizes the value to an integer before commitment. General floating-point values are rejected by the canonicalization profile.

### `Aurora.local(...)`

Creates a local-only SDK client backed by SQLite.

```python
Aurora.local(
    db_path="aurora_agent.db",
    boundaries=[boundary],
)
```

The local path does not require an AURORA server, network access, model provider, or agent framework.

## Lifecycle surface

### `Aurora.propose(...) -> Action`

Creates and persists a `PROPOSED` action record.

```python
action = aurora.propose(
    boundary="payment.execute",
    tool="send_payment",
    arguments={"recipient": "vendor_123", "amount": 25000},
    risk="high",
    authorization_required=True,
)
```

The proposal digest commits the boundary-projected tool invocation under:

- profile: `aurora-agent-action-json`
- version: `0.1`
- hash: `sha256`

### `Action.authorize(...) -> Authorization`

Creates an `AUTHORIZED` grant bound to the exact proposal digest.

```python
authorization = action.authorize(
    approved_by="finance_manager",
    method="human",
)
```

Use only when `authorization_required=True`.

### `Action.policy_pass(...) -> PolicyPass`

Creates a `POLICY_PASSED` grant bound to the exact proposal digest.

```python
policy_pass = action.policy_pass(
    policy_id="payment-threshold-v1",
    decision_reference="decision-123",
)
```

Use only when `authorization_required=False`.

### `Action.execute(...) -> Execution`

Persists `PRECOMMITTED` before it persists `STARTED`, then returns an execution context.

```python
with action.execute(authorization=authorization) as execution:
    result = send_payment(...)
    execution.complete(result=result)
```

Exactly one of `authorization` or `policy_pass` must be supplied. The grant must bind the same action identity and proposal digest.

### `Execution.complete(...)`

Persists `SUCCEEDED` and links the result evidence to the precommit.

```python
execution.complete(
    result={"status": "completed", "operation_id": "pay_123"},
    operation_reference="pay_123",
    outcome_evidence_strength=OutcomeEvidenceStrength.O0,
)
```

`O1`, `O2`, and `O3` require a non-empty `operation_reference`. The SDK records the declared evidence level; it does not independently prove provider or physical-world truth.

### `Execution.fail(...)`

Persists `FAILED` with complete local failure evidence.

### `Execution.unknown(...)`

Persists `UNKNOWN` and preserves incomplete/ambiguous outcome semantics. It must not be converted into success or failure merely because the downstream state is unavailable.

## Evidence export and verification

### `Action.export(path) -> Path`

Creates a deterministic local evidence ZIP containing the action record, lifecycle events, manifest, and mandatory non-claims.

### `Aurora.verify(...)` and `verify_bundle(...)`

Offline verification returns one of:

- `VALID`
- `INVALID`
- `INCOMPLETE`
- `UNSUPPORTED`

```python
report = aurora.verify(
    "action-evidence.zip",
    supplied_arguments={"recipient": "vendor_123", "amount": 25000},
)
```

### CLI

```powershell
aurora-agent-verify action-evidence.zip
```

The CLI verifies the local SDK bundle only. It does not verify an AURORA D5.4 RFC 3161 anchor export.

## Public constants and enums

- `CANONICALIZATION_PROFILE`
- `CANONICALIZATION_VERSION`
- `HASH_ALGORITHM`
- `Phase`
- `OutcomeEvidenceStrength`
- `Verdict`

## Stable v0.1 lifecycle

```text
PROPOSED
→ AUTHORIZED or POLICY_PASSED
→ PRECOMMITTED
→ STARTED
→ SUCCEEDED / FAILED / UNKNOWN
```

The package does not expose a universal action ontology, business-authorization engine, or external timestamp service.

---

# v0.2 ingestion API

## `IngestionClient(...)`

```python
IngestionClient(
    base_url: str,
    api_key: str,
    outbox_path: str | Path = "aurora_ingestion_outbox.db",
)
```

Creates a local-first AURORA transport. The API key is retained only by the in-memory HTTP transport.

### `start_run(...) -> RunSession`

Required: exactly one of `atp_id` or `audit_record_id`.

Important arguments:

- `capture_mode`: `DIGEST_ONLY`, `REDACTED`, or `FULL_PAYLOAD`.
- `runtime`: runtime identity.
- `release_id`: deployed runtime/release identity.
- `boundary_id` and `boundary_version`: declared capture boundary.
- `run_id`: optional stable caller-provided identifier.

Queues `POST /v1/evidence/runs` but does not require immediate network access.

### `resume_run(run_id) -> RunSession`

Reopens a locally persisted run. It does not retrieve a run from AURORA if no local outbox row exists.

### `flush(limit=None) -> list[OutboxItem]`

Recovers stale `SUBMITTING` rows, submits requests in ordinal order, and stops on the first non-acknowledged item. HTTP 200 and 201 become `ACKNOWLEDGED`; 409 becomes `CONFLICT`; retryable server errors return to `PENDING`.

### `replay_request(request_key) -> OutboxItem`

Requeues an acknowledged request using the exact stored canonical bytes and the same idempotency key. It cannot reconstruct or mutate the request. Use this for evidence replay only; it does not re-enter a JAKROW consequential executor.

### `read_run(run_id) -> dict`

Reads the authenticated AURORA ingestion run.

### `verify_run(run_id) -> dict`

Requests server-side recomputation of the finalized graph linked to the run.

## `RunSession.capture(...) -> event_id`

Supported event types:

- `prompt`
- `retrieved_context`
- `model_invocation`
- `model_response`
- `tool_request`
- `authorization`
- `tool_execution`
- `tool_outcome`
- `human_review`
- `final_decision`
- `runtime_failure`

By default, each event parents the previously queued event. Explicit `parent_event_ids` may form a DAG, but every parent must already have a lower sequence.

`runtime_failure` is terminal for staged event capture on the AURORA server and moves the server run to `OUTCOME_UNKNOWN`.

## `RunSession.finalize(...)`

Queues finalization into one immutable graph. `root_event_id` defaults to the last local event. After finalization is queued, the local run rejects additional events.

## `ClaudeAgentCaptureAdapter`

Framework-light methods:

- `prompt`
- `retrieved_context`
- `model_invocation`
- `model_response`
- `authorization`
- `tool_request`
- `tool_execution_started`
- `execute_operation` context manager
- `tool_outcome`
- `runtime_failure`
- `human_review`
- `final_decision`

The adapter does not import Anthropic packages. The application binds these methods to actual Claude Agent SDK hooks.

## `JAKROWD3IngestionObserver`

Implements the optional JAKROW D3 ingestion-observer contract:

- `before_consequence`
- `precondition_failed`
- `terminal`
- `outcome_unknown`

The observer records authorization, tool request, and tool execution evidence to the durable local outbox before the D3 executor is invoked. It records terminal evidence only after JAKROW terminal persistence and continuity verification. Event IDs are deterministic per run, approval, and phase. Recovery can reconstruct missing pre-events from durable STARTED and terminal state without re-invoking the executor. Terminal paths queue `final_decision` and finalization automatically unless `auto_finalize=False` is explicitly selected.

Failure classification is strict:

- a proven pre-consequence abort is recorded as `tool_outcome` with
  `decision_state=FAILED_BEFORE_CONSEQUENCE`;
- only a failure whose consequence result cannot be determined is recorded as
  `runtime_failure`, which moves the AURORA run to `OUTCOME_UNKNOWN`;
- neither evidence path permits automatic consequential retry.

## Local outbox states

```text
PENDING
→ SUBMITTING
→ ACKNOWLEDGED / CONFLICT / REJECTED
```

A crash while `SUBMITTING` is recovered to `PENDING`. Queue ordering is global by insertion ID and stable per run by ordinal.


---

# v0.5 AI Output API

## `AIOutputClient(...)`

Creates, seals, verifies, reads, and downloads bundles for
`auroraseal.evidence` v3 AI Output records.

Methods:

- `create(request, idempotency_key=None)`
- `create_digest_only(...)`
- `create_redacted(...)`
- `create_full_payload(...)`
- `get(record_id)`
- `seal(record_id, idempotency_key=None)`
- `verify(record_id)`
- `download_bundle(record_id, destination)`
- `link_decision(ai_output_record_id, decision_record_id, idempotency_key=None)`
- `get_relationship(link_id)`
- `verify_relationship(link_id)`
- `list_linked_decisions(ai_output_record_id)`
- `list_decision_outputs(decision_record_id)`

Relationship creation requires both source records to be cryptographically
sealed and owned by the same organization. The returned object is a separate
immutable signed registry proof. It does not mutate either source record or
prove causal, legal, policy, fairness, or correctness claims.

See `AI_OUTPUT_QUICKSTART.md`.


---

# v0.6 AI Decision API

## `AIDecisionClient(...)`

Creates, lists, reads, seals, verifies, and downloads offline bundles for
`auroraseal.evidence` v3 AI Decision records.

Methods:

- `list(limit=100, offset=0)`
- `create(request, idempotency_key=None)`
- `get(record_id)`
- `seal(record_id, idempotency_key=None)`
- `verify(record_id)`
- `download_bundle(record_id, destination)`

Contract helpers:

- `canonical_decimal(value)`
- `build_score_interpretation(...)`
- `build_policy_context(...)`
- `build_evidence_flag(...)`
- `build_evidence_assessment(...)`
- `build_ai_decision_request(...)`

Numeric score values are canonical decimal strings. Floats and exponent
notation are rejected. Score, policy, outcome, and completeness fields remain
operator-declared context and do not establish correctness, fairness, legality,
policy applicability, or external-world truth.

See `AI_DECISION_QUICKSTART.md`.