# AURORA self-serve quickstart

Target completion time after account creation: 15 minutes. This target is not
considered externally proven until a first-time user completes the published
path without operator assistance.

## Preconditions

1. Create an account at https://auroraseal.com/sign-up
2. Open https://auroraseal.com/app/quickstart
3. Activate the no-card Sandbox trial when needed.
4. Create a quickstart API key. The raw key is shown once.

## Run

PowerShell:

```powershell
python -m pip install aurora-agent
$env:AURORA_API_KEY = "<shown-once-key>"
aurora-agent quickstart
```

Bash/zsh:

```bash
python -m pip install aurora-agent
export AURORA_API_KEY="<shown-once-key>"
aurora-agent quickstart
```

Expected terminal result:

```text
AURORA self-serve quickstart: PASS
Controlled action executed once : True
Verification                    : VALID
Viewer URL                      : https://auroraseal.com/verify-app/...
```

## What the command does

- creates one controlled sample sealed record through `/v1/audit-records`;
- queues `authorization`, `tool_request`, and `tool_execution` evidence locally;
- performs one local SQLite-ledger operation;
- queues `tool_outcome` and `final_decision`;
- finalizes an immutable compositional evidence graph;
- requests a fresh server-side verification;
- prints the authenticated Viewer URL.

The fixture performs no external payment, message, purchase, or customer action.
`DIGEST_ONLY` is the default. Evidence delivery retry is separate from action retry.
The API key is held only in process memory and is not written to the local outbox.
