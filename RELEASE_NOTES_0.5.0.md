# aurora-agent 0.5.0

Adds signed first-class relationships between sealed AuroraSeal AI Output v3
records and sealed AI decision records.

## Added

- `AIOutputClient.link_decision(...)`
- `AIOutputClient.get_relationship(...)`
- `AIOutputClient.verify_relationship(...)`
- `AIOutputClient.list_linked_decisions(...)`
- `AIOutputClient.list_decision_outputs(...)`
- list-valued JSON response validation for relationship queries

## Evidence boundary

The SDK creates a separate immutable signed registry proof. It does not alter
either source record and does not claim causation, justification, correctness,
fairness, lawfulness, policy validity, or compliance.

## Compatibility

- Compatible with `auroraseal.evidence` schema `3.0`
- Compatible with `auroraseal.ai_output` profile `1.0`
- Relationship profile: `auroraseal.ai_output_decision` `1.0`
- Existing local action evidence, ingestion, and AI Output APIs remain available