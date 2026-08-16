# aurora-agent 0.9.0

- Adds `DataLifecycleClient` for the frozen AuroraSeal Data Lifecycle v1 control plane.
- Adds sealed lifecycle-object creation and supporting artifact registration.
- Adds content-artifact registration for independently replayable lifecycle evidence.
- Adds deterministic operation-intent preparation for destructive lifecycle operations.
- Adds append-only lifecycle-event submission with explicit idempotency keys.
- Adds exact-target lifecycle projection reads bound to record ID, digest, and record type.
- Adds atomic Data Lifecycle Verification Bundle downloads.
- Preserves structured API errors, retry policy, and transport/protocol failure separation.
- Preserves immutable sealed evidence: lifecycle records describe retention, redaction,
  disclosure, tombstone, and erasure evidence without modifying the original record.
- Preserves existing action, ingestion, AI Output, AI Decision, Human Approval, and
  Amendment public APIs.

A verified lifecycle bundle proves the packaged technical projection under the
frozen Stage F profile. It does not prove legal compliance, substantive
correctness, sufficient redaction, or successful erasure beyond the supplied
and independently verifiable evidence.
