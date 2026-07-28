# AuroraSeal AI Output v3 Quickstart

Requires `aurora-agent>=0.5.0` and an AURORA API key with read/write scope.

```python
from aurora_agent import AIOutputClient

client = AIOutputClient(
    base_url="https://aurora-mvp-production.up.railway.app",
    api_key="<shown-once-key>",
)

created = client.create_full_payload(
    input_content={"prompt": "Classify the request"},
    output_content={"label": "manual_review", "confidence": "0.82"},
    output_format="classification",
    model_provider="example-provider",
    model_name="example-model",
    model_version="2026-07-28",
    actor={
        "actor_type": "service",
        "actor_id": "risk-service",
        "actor_role": "ai-output-recorder",
        "application_id": "risk-api",
        "environment": "production",
    },
    inference_parameters={"temperature": "0", "max_tokens": 256},
)

record_id = created["record"]["subject"]["record_id"]
sealed = client.seal(record_id)
verified = client.verify(record_id)
bundle_path = client.download_bundle(record_id, ".")

print(record_id)
print(sealed["record"]["seal"]["seal_state"])
print(verified["status"])
print(bundle_path)

# Link the sealed output to an existing sealed AI decision.
relationship = client.link_decision(record_id, "ATP-20260728-EXAMPLE")
link_id = relationship["subject"]["link_id"]
print(client.verify_relationship(link_id)["status"])
print(client.list_linked_decisions(record_id))
```

## Capture modes

- `create_digest_only(...)`: sends only caller-supplied SHA-256 commitments.
- `create_redacted(...)`: commits source digests and stores redacted JSON.
- `create_full_payload(...)`: sends full JSON and lets AuroraSeal compute digests.

Floats are rejected before transmission. Exact non-integer evidence values must
be represented as strings, for example `"0.82"`.

AuroraSeal verifies record integrity, signature evidence, and the stored RFC 3161
validation result. It does not establish output correctness, fairness,
lawfulness, policy validity, or factual truth.

## AI Output ↔ Decision relationship boundary

`link_decision(...)` creates a separate immutable signed relationship proof.
It does not rewrite the AI Output or decision record. It also does not prove
that the output caused, justified, or made the decision correct. The public
proof URL returned by the API states this boundary explicitly.