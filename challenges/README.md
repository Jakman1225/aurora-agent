# AURORA Evidence Verification Challenges

These challenges are designed to test one narrow claim:

> An RFC 3161 timestamp token can be verified independently of AURORA application code, and a changed payload fails verification against the same token.

No AURORA SDK, backend access, API key, or custom verifier is required. The only required verification tool is OpenSSL.

## Challenges

- [CH-01 — Independent RFC 3161 verification](./ch-01-independent-rfc3161-verification/)
- [CH-02 — One-bit tamper detection](./ch-02-one-bit-tamper-detection/)

## Scope

Successful verification establishes that the supplied timestamp token is cryptographically bound to the supplied payload digest and validates under the supplied TSA certificate chain.

It does not establish:

- that every relevant event was captured;
- that the payload describes a true external-world event;
- that the actor had legal authority;
- that the underlying decision was correct, fair, or compliant;
- qualified electronic timestamp status unless separately established for the applicable trust framework and validation time.

## Required publication step

Before publishing these challenges, replace every placeholder artifact with an export from one real AURORA timestamp record. Do not generate a local self-signed TSA sample and present it as an AURORA production artifact.
