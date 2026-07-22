# AURORA compositional-evidence ingestion quickstart

## 1. Configure without persisting the API key

```powershell
$env:AURORA_BASE_URL = "https://aurora-mvp-production.up.railway.app"
$env:AURORA_API_KEY  = "<new live or sandbox API key>"
```

The API key is passed to the in-memory HTTP transport. It is not stored in the SQLite outbox.

## 2. Capture one generic AI run

```python
import os
from aurora_agent import IngestionClient, ClaudeAgentCaptureAdapter

client = IngestionClient(
    base_url=os.environ["AURORA_BASE_URL"],
    api_key=os.environ["AURORA_API_KEY"],
    outbox_path="aurora_ingestion_outbox.db",
)

run = client.start_run(
    atp_id="ATP-20260701-2934A51E",
    capture_mode="DIGEST_ONLY",
    runtime="claude-agent-sdk",
    release_id="my-agent-2026.07.21",
    boundary_id="my-agent.execute_operation",
    boundary_version="0.1",
)
adapter = ClaudeAgentCaptureAdapter(run)

adapter.prompt({"text": "Review the supplier request"})
adapter.retrieved_context({"source": "policy-v4", "content": "..."})
adapter.model_invocation(model="claude-sonnet", parameters={"temperature": 0})
adapter.model_response({"decision": "requires-approval"})
adapter.authorization(
    approval_ref="apr_0123456789abcdef0123456789abcdef",
    authorization_digest="sha256:" + "a" * 64,
    actor="finance-manager",
)

with adapter.execute_operation(
    tool_name="execute_operation",
    arguments={"target": "supplier", "amount": 100},
    operation_ref="op_123456789abc",
    approval_ref="apr_0123456789abcdef0123456789abcdef",
):
    # Invoke the real tool here.
    pass

adapter.tool_outcome(
    {"status": "SUCCEEDED"},
    operation_ref="op_123456789abc",
)
root = adapter.final_decision({"outcome": "APPROVED"}, actor="reviewer-1")
run.finalize(root_event_id=root)

results = client.flush()
assert all(item.state == "ACKNOWLEDGED" for item in results)
```

The local queue order is fixed:

```text
open run
→ event 0
→ event 1
→ ...
→ finalize
```

A network or process failure resets `SUBMITTING` items to `PENDING` on the next flush. Exact replays retain the same idempotency key and canonical request bytes.

## 3. Privacy modes

### `DIGEST_ONLY` — default

- the source value is canonicalized and committed locally;
- only `sha256:<digest>` enters the outbox and AURORA request;
- the raw value does not enter the outbox database;
- AURORA records `CLIENT_COMMITMENT`, not `SERVER_RECOMPUTED`.

### `REDACTED`

- the original source digest is sent;
- only the explicitly supplied redacted value is stored and transmitted;
- the unredacted raw value does not enter the outbox.

```python
run = client.start_run(
    atp_id="ATP-...",
    capture_mode="REDACTED",
)
run.capture(
    "prompt",
    {"customer_email": "person@example.com", "request": "..."},
    redacted_payload={"customer_email": "[REDACTED]", "request": "..."},
    redacted_payload_present=True,
)
```

### `FULL_PAYLOAD`

- raw JSON enters the local outbox, the API request, and the authenticated graph payload;
- use only under an explicit organizational policy.

## 4. Flush an existing outbox

```powershell
aurora-agent-ingest `
  --base-url $env:AURORA_BASE_URL `
  --api-key $env:AURORA_API_KEY `
  --outbox .\aurora_ingestion_outbox.db
```

List queue status without printing request or response bodies:

```powershell
aurora-agent-ingest --outbox .\aurora_ingestion_outbox.db --list
```

## 5. JAKROW D3 production integration

`JAKROWD3IngestionObserver` attaches to JAKROW's actual `run_d3_execution`
path. The observer writes deterministic local evidence before and after the
consequence boundary. Terminal, deterministic pre-consequence abort, and
outcome-unknown paths queue `final_decision` and finalization automatically.

```python
from aurora_agent import IngestionClient, JAKROWD3IngestionObserver
from evidence_contract.approval_process_c_execute import run_d3_execution

client = IngestionClient(...)
run = client.start_run(
    atp_id="ATP-...",
    runtime="JAKROW-D3",
    capture_mode="DIGEST_ONLY",
)
observer = JAKROWD3IngestionObserver(run)

code, result = run_d3_execution(
    approval_db="approval.db",
    operations_db="operations.db",
    approval_ref="apr_...",
    request_path="request.json",
    ingestion_observer=observer,
)

client.flush()
```

Normal event order:

```text
authorization
→ tool_request
→ tool_execution
→ tool_outcome
→ final_decision
→ finalize
```

Boundary behavior:

- local outbox failure before consequence persists a tamper-evident
  `EXECUTION_ABORTED_BEFORE_CONSEQUENCE` marker and invokes no executor;
- an executor may raise `PreConsequenceExecutionError` only when it can prove
  that no consequence began; this is captured as a known failed
  `tool_outcome`, not as `runtime_failure`;
- arbitrary failure after `STARTED` remains `OUTCOME_UNKNOWN`;
- observer or network failure after consequence never re-invokes the executor;
- a later replay repairs missing evidence from durable JAKROW state with the
  same deterministic event IDs;
- exact evidence replay is separate from consequential execution replay.

Run the controlled production smoke from the JAKROW repository root:

```powershell
$env:AURORA_API_KEY = "<rotated key>"
python .\scripts\smoke_jakrow_production_integration.py `
  --base-url "https://aurora-mvp-production.up.railway.app" `
  --atp-id "ATP-..." `
  --confirm-production-artifact
```

The smoke uses a local append-only controlled executor. It does not perform a
real payment, purchase, notification, or customer operation.

## 6. Verification boundary

Finalization creates one immutable AURORA Compositional Evidence graph and a synthetic `aurora_record` node linked to the selected sealed record. A `VALID` graph verifies stored commitments, node digests, parent links, connectivity, and the record link. It does not prove that no runtime path bypassed the SDK or that a client-supplied digest corresponds to an undisclosed raw value.
