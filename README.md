# AURORA Agent Evidence SDK v0.3

`aurora-agent` is a local-first Python SDK for instrumenting declared consequential machine actions and transmitting compositional execution evidence to AURORA.

It provides two compatible surfaces:

1. **Local action evidence** — proposal, authorization/policy, precommit, execution, outcome, deterministic bundle export, and offline verification.
2. **AURORA ingestion** — incremental runtime events, a durable SQLite outbox, idempotent transport, and finalization into an immutable AURORA Compositional Evidence graph.

Core properties:

- deterministic JSON canonicalization and SHA-256 commitments;
- raw-payload minimization with `DIGEST_ONLY` as the default;
- explicit `REDACTED` and `FULL_PAYLOAD` modes;
- API keys held only in memory and never written to the outbox;
- ordered, crash-recoverable delivery with stable idempotency keys;
- generic Python capture surface;
- framework-light Claude Agent SDK adapter;
- direct observer integration with the JAKROW D3 `execute_operation` path;
- final AURORA graph linked to an existing sealed AURORA record.

Package identity:

- distribution: `aurora-agent`
- import: `aurora_agent`
- version: `0.3.0`
- Python: `>=3.11,<3.14`

Documentation:

- `SELF_SERVE_QUICKSTART.md`
- `QUICKSTART.md`
- `INGESTION_QUICKSTART.md`
- `API_REFERENCE.md`
- `THREAT_MODEL.md`
- `NON_CLAIMS.txt`
- `LICENSE` and `NOTICE`
- `PRIVATE_RELEASE_NOTES.md`

The SDK does not claim capture completeness, absence of bypass paths, causal truth, external-provider acknowledgement, qualified timestamp status, or legal compliance. `DIGEST_ONLY` protects raw values from the local outbox and AURORA payload storage, but AURORA can verify only the supplied client commitment unless the raw value is transmitted separately.

The `aurora-agent` SDK source and its generated Python distributions are licensed under the Apache License, Version 2.0. See `LICENSE`, `NOTICE`, and the repository-root `LICENSING.md`. AURORA hosted services and JAKROW components outside `aurora-agent/` are not included in that grant.

## Self-serve quickstart

After creating a Sandbox API key at `https://auroraseal.com/app/quickstart`:

```bash
pip install aurora-agent
export AURORA_API_KEY="<shown-once-key>"
aurora-agent quickstart
```

The command creates a controlled sample record, commits evidence locally before a
local SQLite ledger operation, finalizes the graph, verifies it server-side, and
prints the authenticated Viewer URL. It does not perform an external payment,
message, purchase, or customer operation. The default capture mode is
`DIGEST_ONLY`; the API key is not persisted in the outbox.

Recovery commands:

```bash
aurora-agent outbox status --outbox PATH
aurora-agent outbox flush --outbox PATH
```

