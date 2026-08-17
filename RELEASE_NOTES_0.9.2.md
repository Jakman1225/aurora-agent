# aurora-agent 0.9.2

This documentation-integrity release restores characters that were replaced
during a Windows-side README edit and advances the package metadata from Alpha
to Beta.

- Restores eight em dashes in the README feature and verification-challenge
  descriptions.
- Declares UTF-8 and LF as the repository editing defaults through
  `.editorconfig`.
- Adds a regression test that rejects replacement characters, literal `??`
  substitutions, and common UTF-8 mojibake in the README.
- Runs the SDK test workflow when README or editor-encoding rules change.
- Changes the Python package development-status classifier from Alpha to Beta.

This release does not change runtime behavior, public APIs, evidence schemas,
canonicalization profiles, bundle contracts, verification semantics, or the
meaning of a VALID verdict.