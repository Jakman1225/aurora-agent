# Five-minute quickstart

## 1. Install the private wheel

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade aurora-agent
```

Python 3.11, 3.12, and 3.13 are supported.

## 2. Instrument one action

```python
from aurora_agent import Aurora, Boundary, FieldRule

boundary = Boundary.strict(
    boundary_id="payment.execute",
    version="0.1",
    tool="send_payment",
    fields={
        "recipient": FieldRule.nonblank_string(),
        "amount": FieldRule.integer_exact(),
    },
    capture_mode="WRAPPED_EXECUTION",
)

aurora = Aurora.local(db_path="evidence.db", boundaries=[boundary])

action = aurora.propose(
    boundary="payment.execute",
    tool="send_payment",
    arguments={"recipient": "vendor_123", "amount": 25000},
    risk="high",
    authorization_required=True,
)

authorization = action.authorize(approved_by="finance_manager")

with action.execute(authorization=authorization) as execution:
    result = {"status": "completed", "operation_id": "pay_123"}
    execution.complete(result=result, operation_reference="pay_123")

bundle = action.export("action-evidence.zip")
report = aurora.verify(bundle)
assert report.verdict.value == "VALID"
```

## 3. Verify from the CLI

```powershell
.\.venv\Scripts\aurora-agent-verify.exe .\action-evidence.zip
```

Expected lifecycle:

```text
PROPOSED
→ AUTHORIZED
→ PRECOMMITTED
→ STARTED
→ SUCCEEDED
```

`VALID` applies only to the exported local SDK evidence bundle and declared capture boundary. It does not prove external anchoring, capture completeness, or qualified timestamp status.