from __future__ import annotations

import argparse
import json
import os

from .ingestion import IngestionClient


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="aurora-agent-ingest")
    parser.add_argument("--base-url", default=os.environ.get("AURORA_BASE_URL"))
    parser.add_argument("--api-key", default=os.environ.get("AURORA_API_KEY"))
    parser.add_argument("--outbox", default="aurora_ingestion_outbox.db")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--list", action="store_true", dest="list_items")
    args = parser.parse_args(argv)
    if args.list_items:
        from .ingestion_outbox import IngestionOutbox

        items = IngestionOutbox(args.outbox).items()
        print(json.dumps([item.__dict__ | {"request_bytes": "<redacted>", "response_bytes": "<redacted>"} for item in items], indent=2))
        return 0
    if not args.base_url or not args.api_key:
        parser.error("--base-url and --api-key (or AURORA_BASE_URL/AURORA_API_KEY) are required")
    client = IngestionClient(
        base_url=args.base_url,
        api_key=args.api_key,
        outbox_path=args.outbox,
    )
    results = client.flush(limit=args.limit)
    print(json.dumps([{"id": item.id, "state": item.state, "status": item.response_status, "error": item.error_code} for item in results], indent=2))
    return 0 if all(item.state == "ACKNOWLEDGED" for item in results) else 2


if __name__ == "__main__":
    raise SystemExit(main())
