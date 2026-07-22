# Private release notes — 0.2.1

> Historical note: this document describes the private 0.2.1 delivery.
> Beginning with version 0.3.0, the `aurora-agent/` SDK is licensed
> under Apache-2.0. See `LICENSE`, `NOTICE`, and `../LICENSING.md`.

Production-integration hardening:

- connected the observer to the actual JAKROW D3 execution boundary;
- deterministic event IDs for restart-safe evidence repair;
- automatic `final_decision` capture and graph finalization;
- exact acknowledged-request replay using stored canonical bytes;
- authenticated run read and server-side run verification helpers;
- deterministic pre-consequence abort evidence distinct from `OUTCOME_UNKNOWN`;
- immediate unknown capture for post-dispatch exceptions without a second D3
  invocation;
- terminal recovery from durable JAKROW state without consequential replay;
- controlled exactly-once executor fixture and production smoke script;
- failure-injection coverage for outbox failure, network outage, post-terminal observer failure, and STARTED-without-terminal recovery.

Compatibility:

- local v0.1 action evidence APIs and verifier remain available;
- AURORA Evidence Ingestion v0.1 routes and schema are unchanged;
- no AURORA database migration is required for this patch;
- this 0.2.1 delivery was private and predates the Apache-2.0 grant introduced for 0.3.0.
