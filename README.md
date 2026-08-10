# AURORA Agent Evidence SDK

[![PyPI](https://img.shields.io/pypi/v/aurora-agent)](https://pypi.org/project/aurora-agent/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)

`aurora-agent` is a local-first Python SDK for instrumenting declared
consequential machine actions and transmitting compositional execution evidence
to [AURORA](https://auroraseal.com).

```bash
pip install aurora-agent
```

It provides five compatible surfaces:

1. **Local action evidence** — proposal, authorization/policy, precommit,
   execution, outcome, deterministic bundle export, and offline verification.
2. **AURORA ingestion** — incremental runtime events, a durable SQLite outbox,
   idempotent transport, and finalization into an immutable AURORA
   Compositional Evidence graph.
3. **AI Output evidence** — first-class AuroraSeal v3 records with digest-only,
   redacted, or full-payload capture, sealing, verification, bundle export, and
   signed links to sealed AI decision records.
4. **AI Decision evidence** — first-class decision records that preserve
   operator-declared score interpretation, policy context, evidence gaps,
   uncertainty, sealing, public metadata verification, and offline bundles.
5. **Human Approval evidence** — policy-bound approval requirements, immutable
   review events, reviewer eligibility, deterministic single- and multi-party
   process projections, and human-bound approval operations.

## Core properties

- deterministic JSON canonicalization and SHA-256 commitments;
- raw-payload minimization with `DIGEST_ONLY` as the default;
- explicit `REDACTED` and `FULL_PAYLOAD` modes;
- API keys held only in memory and never written to the outbox;
- ordered, crash-recoverable delivery with stable idempotency keys;
- generic Python capture surface;
- framework-light Claude Agent SDK adapter;
- direct observer integration with the JAKROW D3 `execute_operation` path;
- final AURORA graph linked to an existing sealed AURORA record.

## Self-serve quickstart

After creating a Sandbox API key at
`https://auroraseal.com/app/quickstart`:

```bash
pip install aurora-agent
export AURORA_API_KEY="<shown-once-key>"
aurora-agent quickstart
```

The command creates a controlled sample record, commits evidence locally before
a local SQLite ledger operation, finalizes the graph, verifies it server-side,
and prints the authenticated Viewer URL.

It does not perform an external payment, message, purchase, or customer
operation. The default capture mode is `DIGEST_ONLY`; the API key is not
persisted in the outbox.

Recovery commands:

```bash
aurora-agent outbox status --outbox PATH
aurora-agent outbox flush --outbox PATH
```

## Package identity

| | |
| --- | --- |
| distribution | `aurora-agent` |
| import | `aurora_agent` |
| version | `0.7.0` |
| Python | `>=3.11,<3.14` |

## Documentation

- [`HUMAN_APPROVAL_QUICKSTART.md`](HUMAN_APPROVAL_QUICKSTART.md)
- [`AI_DECISION_QUICKSTART.md`](AI_DECISION_QUICKSTART.md)
- [`AI_OUTPUT_QUICKSTART.md`](AI_OUTPUT_QUICKSTART.md)
- [`SELF_SERVE_QUICKSTART.md`](SELF_SERVE_QUICKSTART.md)
- [`QUICKSTART.md`](QUICKSTART.md)
- [`INGESTION_QUICKSTART.md`](INGESTION_QUICKSTART.md)
- [`API_REFERENCE.md`](API_REFERENCE.md)
- [`THREAT_MODEL.md`](THREAT_MODEL.md)
- [`NON_CLAIMS.txt`](NON_CLAIMS.txt)
- [`RELEASE_NOTES_0.7.0.md`](RELEASE_NOTES_0.7.0.md)
- [`RELEASE_NOTES_0.6.0.md`](RELEASE_NOTES_0.6.0.md)
- [`RELEASE_NOTES_0.5.0.md`](RELEASE_NOTES_0.5.0.md)
- [`RELEASE_NOTES_0.4.0.md`](RELEASE_NOTES_0.4.0.md)
- [`RELEASE_NOTES_0.3.1.md`](RELEASE_NOTES_0.3.1.md)
- [`RELEASE_NOTES_0.3.0.md`](RELEASE_NOTES_0.3.0.md)

## Independent verification challenges

AURORA publishes two OpenSSL-only RFC 3161 verification exercises. They require
no AURORA SDK, API key, backend access, or custom verifier.

- [CH-01 — Independently verify the timestamp token](./challenges/ch-01-independent-rfc3161-verification/)
- [CH-02 — Change one bit and reproduce verification failure](./challenges/ch-02-one-bit-tamper-detection/)

These challenges demonstrate cryptographic binding and tamper detection for the
supplied bytes.

They do not establish capture completeness, legal authority, decision
correctness, external-provider acknowledgement, or external-world truth.

## What this SDK does not claim

The SDK does not claim:

- capture completeness;
- absence of bypass paths;
- causal or external-world truth;
- external-provider acknowledgement;
- qualified timestamp status;
- legal authority or legal compliance;
- decision correctness, fairness, or policy validity.

`DIGEST_ONLY` protects raw values from the local outbox and AURORA payload
storage, but AURORA can verify only the supplied client commitment unless the
raw value is transmitted separately.

## License

The `aurora-agent` SDK source and its generated Python distributions are
licensed under the Apache License, Version 2.0. See [`LICENSE`](LICENSE) and
[`NOTICE`](NOTICE).

AURORA hosted services, the AURORA backend and frontend, and JAKROW components
outside this SDK are proprietary and are not included in that grant. See
[`LICENSE_HISTORY.md`](LICENSE_HISTORY.md) for the licensing history of releases
before 0.3.0.

AURORA and JAKROW are marks of Krow Industries.