from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from . import __version__
from .ingestion import IngestionClient
from .ingestion_outbox import IngestionOutbox
from .quickstart import (
    DEFAULT_API_BASE_URL,
    DEFAULT_FRONTEND_URL,
    QuickstartError,
    QuickstartRunner,
)


def _print_quickstart(result, *, json_output: bool) -> None:
    if json_output:
        print(json.dumps(result.to_dict(), sort_keys=True, indent=2))
        return
    print("AURORA self-serve quickstart: PASS")
    print(f"Controlled action executed once : {result.executor_invocation_count == 1}")
    print(f"Evidence finalized              : {result.graph_id}")
    print(f"Verification                    : {result.verification_verdict}")
    print(f"Capture mode                    : {result.capture_mode}")
    print(f"Viewer URL                      : {result.viewer_url}")
    print(f"Public verification             : {result.public_verify_url}")
    print(f"Workspace                       : {result.workspace}")


def _init_command(args) -> int:
    target = Path(args.directory)
    target.mkdir(parents=True, exist_ok=True)
    readme = target / "AURORA_QUICKSTART.txt"
    if readme.exists() and not args.force:
        print(f"Already exists: {readme}")
        return 0
    readme.write_text(
        "AURORA self-serve quickstart\n"
        "============================\n\n"
        "1. Create an API key in https://auroraseal.com/app/quickstart\n"
        "2. Set AURORA_API_KEY in your shell. Do not commit it.\n"
        "3. Run: aurora-agent quickstart\n\n"
        "The quickstart creates one controlled sample record and one local-ledger\n"
        "operation, finalizes an evidence graph, verifies it, and prints the viewer URL.\n",
        encoding="utf-8",
    )
    print(f"Created {readme}")
    print("Next: set AURORA_API_KEY, then run `aurora-agent quickstart`.")
    return 0


def _quickstart_command(args) -> int:
    api_key = args.api_key or os.environ.get("AURORA_API_KEY")
    if not api_key:
        raise QuickstartError(
            "AURORA_API_KEY is required. Create one at "
            "https://auroraseal.com/app/quickstart and set it in the current shell."
        )
    result = QuickstartRunner(
        api_key=api_key,
        base_url=args.base_url,
        frontend_url=args.frontend_url,
        workspace=args.workspace,
    ).run()
    _print_quickstart(result, json_output=args.json)
    return 0


def _outbox_status(args) -> int:
    items = IngestionOutbox(args.outbox).items()
    state_counts: dict[str, int] = {}
    for item in items:
        state_counts[item.state] = state_counts.get(item.state, 0) + 1
    payload = {
        "outbox": str(Path(args.outbox).resolve()),
        "item_count": len(items),
        "state_counts": dict(sorted(state_counts.items())),
        "items": [
            {
                "id": item.id,
                "request_key": item.request_key,
                "run_id": item.run_id,
                "state": item.state,
                "attempts": item.attempts,
                "response_status": item.response_status,
                "error_code": item.error_code,
            }
            for item in items
        ],
    }
    print(json.dumps(payload, sort_keys=True, indent=2))
    return 0


def _outbox_flush(args) -> int:
    api_key = args.api_key or os.environ.get("AURORA_API_KEY")
    if not api_key:
        raise QuickstartError("AURORA_API_KEY is required to flush the evidence outbox")
    client = IngestionClient(
        base_url=args.base_url,
        api_key=api_key,
        outbox_path=args.outbox,
    )
    results = client.flush(limit=args.limit)
    payload = [
        {
            "id": item.id,
            "request_key": item.request_key,
            "state": item.state,
            "status": item.response_status,
            "error": item.error_code,
        }
        for item in results
    ]
    print(json.dumps(payload, sort_keys=True, indent=2))
    return 0 if all(item.state == "ACKNOWLEDGED" for item in results) else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aurora-agent",
        description="AURORA runtime evidence SDK command line",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="write a local quickstart instruction file")
    init.add_argument("--directory", default=".aurora-quickstart")
    init.add_argument("--force", action="store_true")
    init.set_defaults(func=_init_command)

    quick = sub.add_parser(
        "quickstart",
        help="create a sample record, execute one controlled local action, and verify the graph",
    )
    quick.add_argument("--base-url", default=os.environ.get("AURORA_BASE_URL", DEFAULT_API_BASE_URL))
    quick.add_argument("--frontend-url", default=os.environ.get("AURORA_FRONTEND_URL", DEFAULT_FRONTEND_URL))
    quick.add_argument("--api-key", default=None, help=argparse.SUPPRESS)
    quick.add_argument("--workspace", default=".aurora-quickstart")
    quick.add_argument("--json", action="store_true")
    quick.set_defaults(func=_quickstart_command)

    outbox = sub.add_parser("outbox", help="inspect or flush the durable evidence outbox")
    outsub = outbox.add_subparsers(dest="outbox_command", required=True)
    status = outsub.add_parser("status", help="show redacted outbox state")
    status.add_argument("--outbox", default="aurora_ingestion_outbox.db")
    status.set_defaults(func=_outbox_status)
    flush = outsub.add_parser("flush", help="retry pending evidence delivery without retrying the action")
    flush.add_argument("--base-url", default=os.environ.get("AURORA_BASE_URL", DEFAULT_API_BASE_URL))
    flush.add_argument("--api-key", default=None, help=argparse.SUPPRESS)
    flush.add_argument("--outbox", default="aurora_ingestion_outbox.db")
    flush.add_argument("--limit", type=int, default=None)
    flush.set_defaults(func=_outbox_flush)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (QuickstartError, ValueError) as exc:
        parser.exit(2, f"aurora-agent: error: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
