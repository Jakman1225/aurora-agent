# aurora-agent 0.3.1

Release date: 2026-07-24

`aurora-agent` 0.3.1 is a supply-chain trust and independent-verification
release. It does not introduce an intentional breaking change to the public
runtime API.

## Added

- GitHub-native build provenance attestations for release artifacts.
- CycloneDX SBOM generation for the published Python distribution.
- SHA-256 checksum generation for wheel, source distribution, and SBOM assets.
- Automatic attachment of release artifacts to the corresponding GitHub
  Release.
- CH-01, an OpenSSL-only RFC 3161 independent timestamp verification exercise.
- CH-02, a one-bit tamper-detection exercise that demonstrates rejection of a
  modified payload.

## Changed

- Generalized the production release workflow so the Git tag is validated
  against the version declared in `pyproject.toml`.
- Preserved the explicit manual distribution-approval gate for production PyPI
  publication.
- Updated package identity and documentation references from 0.3.0 to 0.3.1.
- Updated the public distribution approval record for the 0.3.1 release scope.
- Expanded the source distribution manifest to include public documentation,
  release notes, examples, and verification challenges.
- Kept historical 0.3.0 release notes and the pre-0.3.0 licensing boundary.

## Fixed

- Corrected the source distribution manifest to reference
  `DISTRIBUTION_APPROVAL.txt`.
- Corrected the public distribution-license test to use the current approval
  record filename.
- Corrected the public/private test boundary for the JAKROW production
  integration test. The test is skipped only when the proprietary
  `evidence_contract` package is absent.
- Removed standalone-repository path assumptions from the public GitHub Actions
  workflows.

## Verification

Local release preparation completed with:

- 38 tests passed;
- 1 proprietary JAKROW integration test skipped as intended;
- wheel build passed;
- source distribution build passed;
- `twine check` passed;
- CH-01 independent RFC 3161 verification passed;
- CH-02 one-bit tamper rejection passed.

## Scope and non-claims

The verification challenges demonstrate cryptographic binding and tamper
detection for the supplied bytes. They do not establish capture completeness,
legal authority, decision correctness, qualified timestamp status, regulatory
compliance, or external-world truth.

The Apache-2.0 license applies to the public `aurora-agent` SDK source and its
generated Python distributions. AURORA hosted services, backend, frontend,
infrastructure, and JAKROW components outside this public repository remain
outside that license grant.