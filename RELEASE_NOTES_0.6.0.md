# aurora-agent 0.6.0

- Adds `AIDecisionClient` for first-class AuroraSeal AI Decision v3 records.
- Adds create, list, read, seal, verify, and offline bundle methods.
- Adds strict helpers for canonical decimal score interpretation, policy
  snapshots, evidence flags, evidence assessment, and decision requests.
- Rejects Python floats and malformed score/policy declarations before network
  transmission.
- Preserves the boundary that declared decision context is not a claim of
  correctness, fairness, legality, policy validity, or external-world truth.