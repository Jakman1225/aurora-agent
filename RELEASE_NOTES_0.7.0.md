# aurora-agent 0.7.0

- Adds `HumanApprovalClient` for AuroraSeal Human Approval v1.0 programmatic
  surfaces.
- Adds policy-requirement registration and listing, approval-gate reads,
  immutable review-event reads, process projection, reviewer eligibility, and
  review write methods.
- Adds `approve_from_eligibility()` so callers consume AURORA's exact
  server-provided approval submission instead of predicting sequence or
  resulting state.
- Adds strict builders for the frozen Approval Requirement v1 shape and
  immutable policy-requirement binding request.
- Keeps AURORA API keys and human reviewer tokens as separate credentials. The
  reviewer token is sent only for human-bound operations and is not written to
  the SDK outbox.
- Exposes the deployed Human Approval states, including
  `second_reviewer_required` and `multi_party_approved`, without introducing a
  client-side workflow engine.
- AI Decision verification bundles obtained through `AIDecisionClient` can now
  contain the Human Approval evidence added by AuroraSeal Stage D-5.
- Preserves existing local action, ingestion, AI Output, and AI Decision APIs.

This release does not make approval correctness, legal-authority, policy
adequacy, compliance, or qualified-timestamp claims. Human Approval evaluation
and evidence sealing remain server-authoritative.