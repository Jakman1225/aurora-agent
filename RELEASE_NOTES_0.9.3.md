# aurora-agent 0.9.3

This reliability release reconciles the public SDK with AURORA's current seal
and audit-record creation contracts.

- Adds `seal_standard(...)` to the AI Output and AI Decision clients for
  explicit Standard batch-anchoring admission.
- Sends a stable `Idempotency-Key` when the self-serve quickstart creates its
  sample audit record, so repeating the same operation request cannot create a
  second record.
- Derives Quickstart producer metadata from the package version authority, so
  evidence created by 0.9.3 no longer identifies the historical 0.3.1 release.
- Updates installation examples to use `python -m pip install --upgrade
  aurora-agent`, avoiding accidental execution of an older cached release.
- Adds regression coverage for the two Standard seal methods and the
  quickstart audit-record idempotency key.

This release does not change evidence schemas, canonicalization profiles,
bundle contracts, verification semantics, capture-completeness boundaries, or
the meaning of a VALID verdict.