# aurora-agent 0.8.0

- Adds `AmendmentClient` for AuroraSeal Amendment v1.0 and lifecycle surfaces.
- Adds authoritative lifecycle projection reads that keep cryptographic validity
  separate from operational currentness.
- Adds full AI Output and AI Decision successor preparation. Successors are
  complete new sealed subjects; predecessor fields are never patched or
  implicitly inherited.
- Adds coordinated amendment sealing against the exact server-returned expected
  chain head. Stale heads fail with `AMENDMENT_CHAIN_HEAD_CONFLICT`.
- Adds `build_amendment_request_from_lifecycle()` so callers consume the
  authoritative expected head rather than predicting chain sequence locally.
- Adds authenticated Lifecycle Verification Bundle download.
- Preserves Stage D Human Approval semantics: approval evidence bound to a
  predecessor digest is never inherited by a successor.
- Preserves existing action, ingestion, AI Output, AI Decision, and Human
  Approval public APIs.

A valid Amendment lifecycle proves integrity, chronology, and the recorded
operational currentness of sealed evidence. It does not prove that a correction,
reversal, supersession, amendment, or withdrawal was substantively correct,
lawful, required, or compliant.