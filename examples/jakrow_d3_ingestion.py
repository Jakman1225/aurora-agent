"""Run one already-authorized JAKROW D3 action with AURORA ingestion.

This example requires both private packages to be installed:

    pip install aurora-agent
    pip install -e ../

It never accepts an API key on the command line. Use ``AURORA_API_KEY``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from aurora_agent import IngestionClient, JAKROWD3IngestionObserver
from evidence_contract.approval_process_c_execute import run_d3_execution


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=os.environ.get("AURORA_BASE_URL"))
    parser.add_argument("--atp-id", required=True)
    parser.add_argument("--outbox", default="aurora_ingestion_outbox.db")
    parser.add_argument("--run-id")
    parser.add_argument("--approval-db", required=True)
    parser.add_argument("--operations-db", required=True)
    parser.add_argument("--approval-ref", required=True)
    parser.add_argument("--request", required=True)
    args = parser.parse_args(argv)

    api_key = os.environ.get("AURORA_API_KEY")
    if not args.base_url or not api_key:
        parser.error("AURORA_BASE_URL/--base-url and AURORA_API_KEY are required")

    client = IngestionClient(
        base_url=args.base_url,
        api_key=api_key,
        outbox_path=args.outbox,
    )
    run = client.start_run(
        atp_id=args.atp_id,
        run_id=args.run_id,
        capture_mode="DIGEST_ONLY",
        runtime="JAKROW-D3",
        release_id="jakrow-d3-compositional-ingestion-v0.1",
        boundary_id="jakrow.claude-agent-sdk.pretooluse.execute_operation",
        boundary_version="0.1",
        source="JAKROW",
    )
    observer = JAKROWD3IngestionObserver(run)
    code, result = run_d3_execution(
        approval_db=args.approval_db,
        operations_db=args.operations_db,
        approval_ref=args.approval_ref,
        request_path=args.request,
        ingestion_observer=observer,
    )

    # The observer queues final_decision + finalize locally after a durable
    # terminal or honest unknown state. Network delivery is separate.
    transport_results = client.flush()
    output = {
        "d3_exit_code": code,
        "d3_result": result,
        "ingestion": [
            {
                "request_key": item.request_key,
                "state": item.state,
                "http_status": item.response_status,
                "error_code": item.error_code,
            }
            for item in transport_results
        ],
        "outbox": str(client.outbox.path.resolve()),
    }
    print(json.dumps(output, indent=2, sort_keys=True))

    if any(item.state != "ACKNOWLEDGED" for item in transport_results):
        return 40
    return code


if __name__ == "__main__":
    raise SystemExit(main())
