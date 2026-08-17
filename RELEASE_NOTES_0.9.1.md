# aurora-agent 0.9.1

This compatibility release separates package release identity from the frozen
evidence-bundle and verification-report contract identities.

- Uses one package version source for runtime exports and distribution metadata.
- Records the installed package version as the bundle producer version.
- Records the installed package version as the verifier implementation version.
- Accepts well-formed legacy and current producer versions while continuing to
  enforce evidence-bundle schema v0.1 and canonicalization profile v0.1.
- Updates release workflows to resolve dynamic package metadata and bind the
  selected tag to the explicit distribution-approval record.
- Preserves the existing public action, ingestion, AI Output, AI Decision,
  Human Approval, Amendment, and Data Lifecycle APIs.

This release does not change the meaning of a VALID verdict. It does not prove
capture completeness, absence of bypass paths, external-world truth, qualified
timestamp status, or legal admissibility.