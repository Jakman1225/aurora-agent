# aurora-agent 0.10.0

This release adds clients for approval and execution evidence and updates
Quickstart to wait for a verified external timestamp.

- Tracks evidence registration, approval status, sealing, and receipt recovery.
- Supports exporting verification bundles and managing public verification links.
- Uses a persistent journal to read execution status after an interruption
  without resending the execution request.
- Waits for the sample audit record to be sealed before starting evidence
  ingestion. If the timestamp is still pending after 90 seconds, Quickstart
  reports the record ID for follow-up.
- Adds regression coverage for pending timestamps, successful sealing, and
  the Quickstart timeout.

The execution evidence clients are available through the `stage-g` extra.
Quickstart timestamp polling requires a compatible AURORA backend and an API
key with read and write access.
