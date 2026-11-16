# aurora-agent 0.9.4

This reliability release updates the self-serve quickstart for audit records
that are waiting for a verified external timestamp.

- Checks the status of the same sample record when sealing is pending.
- Waits for sealing before starting the local action and delivering evidence.
- Reports the record identifier if the timestamp is still pending at the
  end of the wait.
- Adds regression coverage for pending, timed-out, and internally timestamped
  sample records.
