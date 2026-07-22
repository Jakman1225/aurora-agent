from pathlib import Path

from aurora_agent import Aurora, Boundary, FieldRule, Verdict

root = Path("example-output")
root.mkdir(exist_ok=True)

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
client = Aurora.local(db_path=root / "evidence.db", boundaries=[boundary])
action = client.propose(
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
bundle = action.export(root / "action-evidence.zip")
report = client.verify(bundle)
print(report.verdict.value)
raise SystemExit(0 if report.verdict is Verdict.VALID else 1)
