# AI Decision v3 quickstart

`aurora-agent` 0.6 adds an API-key client for first-class
`auroraseal.ai_decision` v1.0 records under `auroraseal.evidence` v3.0.

```python
from aurora_agent import (
    AIDecisionClient,
    build_ai_decision_request,
    build_evidence_assessment,
    build_policy_context,
    build_score_interpretation,
)

client = AIDecisionClient(
    base_url="https://aurora-mvp-production.up.railway.app",
    api_key="<AURORA_API_KEY>",
)

score = build_score_interpretation(
    score_value="0.82",
    scale_kind="bounded_numeric",
    transform="probability",
    minimum="0",
    maximum="1",
    score_direction="higher_is_riskier",
    score_source="risk-model-v4",
    threshold_value="0.75",
    threshold_meaning="manual review threshold",
    risk_band="high",
)

policy = build_policy_context(
    policy_id="credit-risk",
    policy_name="Credit Risk Review",
    policy_version="4.2",
    policy_digest="sha256:" + "a" * 64,
    effective_at="2026-07-29T12:00:00.000000Z",
)

request = build_ai_decision_request(
    decision_type="risk_review",
    declared_outcome="manual_review",
    decision_reason="The declared score exceeded the review threshold.",
    decided_at="2026-07-29T12:01:00.000000Z",
    source_output_ids=["ase_source_1"],
    score_interpretation=score,
    policy_contexts=[policy],
    evidence_assessment=build_evidence_assessment(
        evidence_completeness="complete"
    ),
    actor={
        "actor_type": "service",
        "actor_id": "decision-service",
        "actor_role": "policy-evaluator",
    },
    privacy={
        "contains_personal_data": False,
        "redaction_status": "not_applicable",
        "legal_hold_status": "not_applicable",
        "public_display_mode": "metadata_only",
    },
)

created = client.create(request)
record_id = created["record"]["subject"]["record_id"]
client.seal(record_id)
verification = client.verify(record_id)
client.download_bundle(record_id, ".")
print(verification["status"])
```

`seal(...)` requests immediate signature and timestamp evidence. To admit a
DIGESTED decision to Standard batch anchoring instead, call
`client.seal_standard(record_id)`. Both writes accept an optional stable
`idempotency_key` for safe request replay.

All numeric score values are canonical decimal strings. Python floats are
rejected before transmission. The client records operator-declared score,
policy, outcome, and evidence context; it does not establish correctness,
fairness, legality, policy applicability, or causal truth.