# aurora-agent 0.3.0

## Self-serve quickstart

- adds the `aurora-agent` command;
- adds `aurora-agent init`;
- adds `aurora-agent quickstart`;
- adds redacted `aurora-agent outbox status`;
- adds evidence-only `aurora-agent outbox flush`;
- creates a controlled sample AURORA record without a pre-existing ATP ID;
- commits DIGEST_ONLY evidence to the local SQLite outbox before the controlled local-ledger action;
- finalizes and verifies the immutable evidence graph;
- prints public verification and authenticated Viewer URLs;
- adds clean-room cross-repository contract coverage;
- adds Python 3.11-3.13 CI and a guarded PyPI Trusted Publishing workflow.

## Scope

The quickstart performs one controlled local SQLite-ledger operation. It does not
perform a payment, purchase, external message, or customer-side action. VALID
means the stored evidence commitments recompute consistently; it does not prove
decision correctness, legality, fairness, compliance, complete capture, or an
external-world effect.

## Distribution

The `aurora-agent` SDK and its generated Python distributions are licensed under
the Apache License, Version 2.0. This grant is limited to the `aurora-agent/`
directory and does not include AURORA hosted services, JAKROW components outside
that directory, or AURORA/JAKROW trademarks. Public PyPI publication still
requires the protected `pypi` GitHub environment, matching Trusted Publisher
configuration, and a successful clean installation from the public index before
the public Quickstart page is deployed.
