# AURORA Agent Evidence SDK v0.1 — Threat Model

## Protected properties

Within a declared local SDK boundary, v0.1 is designed to detect:

- mutation of committed tool arguments;
- authorization or policy-pass reuse against a different proposal;
- lifecycle reordering;
- missing precommit before `STARTED`;
- mutation of protected event bodies;
- terminal evidence that does not link to the precommit;
- mutation of committed result observations;
- unsupported canonicalization or schema metadata;
- removal of mandatory non-claims from an exported bundle.

## Trust boundary

The SDK protects only representations that enter a configured `Boundary` and are persisted through its lifecycle APIs.

The SDK does not establish that:

- every consequential action crossed the boundary;
- no alternate client, credential, network route, or execution path bypassed capture;
- supplied data corresponded to physical reality;
- a local operation reference exists at an external provider;
- an actor was legally authorized;
- a local clock is an external timestamp.

## Attacker capabilities considered

The local verifier assumes an attacker may obtain an exported ZIP and attempt to:

- edit action arguments;
- edit result observations;
- recompute the outer manifest;
- reorder or replace lifecycle events;
- substitute an authorization or policy-pass reference;
- remove evidence files;
- change canonicalization metadata;
- remove limitation language.

A manifest-only rewrite does not restore validity because the verifier recomputes protected commitments and lifecycle links.

## Out of scope

- host or kernel compromise before evidence persistence;
- malicious modification of the installed SDK implementation;
- key management and code-signing trust for package distribution;
- AURORA backend authentication and tenant isolation;
- D5.3 durable transport;
- RFC 3161 anchoring;
- certificate-path and revocation verification;
- qualified timestamp status;
- general Claude Agent SDK interception;
- universal concurrency or distributed causal ordering;
- legal admissibility or liability allocation.

## Deployment guidance

Stronger evidence requires a capture point closer to the consequential side effect. `SDK_SELF_REPORT` is weaker than a framework hook or wrapped execution interface. The configured `capture_mode` must describe the actual deployment boundary rather than the desired marketing claim.
