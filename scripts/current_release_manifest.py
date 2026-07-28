#!/usr/bin/env python3
"""Generate or verify a cross-platform SHA-256 manifest for the Git index.

The Git index is used as the canonical source because Git has already applied
repository text/eol rules there. Verification additionally fails when a listed
file has unstaged working-tree changes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

SUMS_NAME = "SOURCE_SHA256SUMS.txt"
META_NAME = "CURRENT_RELEASE_MANIFEST.json"
EXCLUDED = {SUMS_NAME, META_NAME}


def run_git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=check,
    )


def repo_root() -> Path:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
        text=True,
    )
    return Path(result.stdout.strip()).resolve()


def tracked_paths(root: Path) -> list[str]:
    raw = run_git(root, "ls-files", "-z").stdout
    paths = [p.decode("utf-8", "surrogateescape") for p in raw.split(b"\0") if p]
    selected: list[str] = []
    for path in paths:
        if path in EXCLUDED:
            continue
        ignored = run_git(root, "check-ignore", "-q", "--no-index", "--", path, check=False)
        if ignored.returncode == 0:
            continue
        if not (root / path).is_file():
            raise RuntimeError(f"tracked file is missing from the working tree: {path}")
        selected.append(path)
    return sorted(selected, key=lambda value: value.encode("utf-8", "surrogateescape"))


def index_bytes(root: Path, path: str) -> bytes:
    result = run_git(root, "show", f":{path}", check=False)
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", "replace").strip()
        raise RuntimeError(f"cannot read index content for {path}: {message}")
    return result.stdout


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build_entries(root: Path) -> list[tuple[str, str]]:
    return [(sha256(index_bytes(root, path)), path) for path in tracked_paths(root)]


def render(entries: list[tuple[str, str]]) -> bytes:
    return "".join(f"{digest}  {path}\n" for digest, path in entries).encode("utf-8")


def generate(root: Path) -> None:
    entries = build_entries(root)
    sums_bytes = render(entries)
    (root / SUMS_NAME).write_bytes(sums_bytes)

    head = run_git(root, "rev-parse", "HEAD").stdout.decode().strip()
    metadata = {
        "schema": "aurora.current-release-manifest.v1",
        "snapshot_basis": "git_index",
        "base_head": head,
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "digest_algorithm": "SHA-256",
        "canonical_content": "Git index blob bytes after repository clean/eol filters",
        "excluded_outputs": sorted(EXCLUDED),
        "file_count": len(entries),
        "source_sums_file": SUMS_NAME,
        "source_sums_sha256": sha256(sums_bytes),
    }
    (root / META_NAME).write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"generated {SUMS_NAME}: {len(entries)} files")
    print(f"generated {META_NAME}")


def parse_sums(path: Path) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line:
            continue
        try:
            digest, file_path = line.split("  ", 1)
        except ValueError as exc:
            raise RuntimeError(f"invalid manifest line {number}") from exc
        if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
            raise RuntimeError(f"invalid SHA-256 at line {number}")
        entries.append((digest, file_path))
    return entries


def verify(root: Path) -> None:
    sums_path = root / SUMS_NAME
    meta_path = root / META_NAME
    if not sums_path.is_file() or not meta_path.is_file():
        raise RuntimeError("current release manifest files are missing")

    expected = parse_sums(sums_path)
    actual = build_entries(root)
    if expected != actual:
        expected_map = {path: digest for digest, path in expected}
        actual_map = {path: digest for digest, path in actual}
        paths = sorted(set(expected_map) | set(actual_map))
        problems = []
        for path in paths:
            if expected_map.get(path) != actual_map.get(path):
                problems.append(path)
        preview = "\n".join(f"  - {p}" for p in problems[:30])
        raise RuntimeError(f"manifest mismatch in {len(problems)} file(s):\n{preview}")

    dirty = run_git(root, "diff", "--name-only", "--", *[path for _, path in expected]).stdout
    if dirty.strip():
        names = dirty.decode("utf-8", "replace").splitlines()
        preview = "\n".join(f"  - {p}" for p in names[:30])
        raise RuntimeError(f"working tree differs from the indexed snapshot:\n{preview}")

    metadata = json.loads(meta_path.read_text(encoding="utf-8"))
    sums_digest = sha256(sums_path.read_bytes())
    if metadata.get("source_sums_sha256") != sums_digest:
        raise RuntimeError("CURRENT_RELEASE_MANIFEST.json does not match SOURCE_SHA256SUMS.txt")
    if metadata.get("file_count") != len(expected):
        raise RuntimeError("CURRENT_RELEASE_MANIFEST.json file_count is incorrect")

    print(f"current release manifest: OK ({len(expected)} files)")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("generate", "verify"))
    args = parser.parse_args()
    root = repo_root()
    try:
        if args.command == "generate":
            generate(root)
        else:
            verify(root)
    except (RuntimeError, OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())