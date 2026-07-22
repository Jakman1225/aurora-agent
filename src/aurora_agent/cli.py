from __future__ import annotations

import argparse
import json
from pathlib import Path

from .model import Verdict
from .verifier import format_report, verify_bundle

_EXIT = {
    Verdict.VALID: 0,
    Verdict.INVALID: 10,
    Verdict.INCOMPLETE: 20,
    Verdict.UNSUPPORTED: 30,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aurora-agent-verify")
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args(argv)
    report = verify_bundle(args.bundle)
    print(format_report(report))
    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(
            json.dumps(report.to_dict(), sort_keys=True, indent=2, ensure_ascii=True) + "\n",
            encoding="utf-8",
        )
    return _EXIT[report.verdict]
