#!/usr/bin/env bash
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ARTIFACTS="$HERE/artifacts"

required=(
  payload.bin
  timestamp-token.tst
  tsa-root.pem
  tsa-intermediates.pem
  expected-payload-sha256.txt
  MANIFEST.sha256
)

for name in "${required[@]}"; do
  if [[ ! -s "$ARTIFACTS/$name" ]]; then
    echo "Missing or empty artifact: artifacts/$name" >&2
    exit 2
  fi
done

(
  cd "$ARTIFACTS"
  sha256sum --check MANIFEST.sha256
)

actual_digest="$(sha256sum "$ARTIFACTS/payload.bin" | awk '{print $1}')"
expected_digest="$(tr -d '[:space:]' < "$ARTIFACTS/expected-payload-sha256.txt")"

if [[ "$actual_digest" != "$expected_digest" ]]; then
  echo "Payload SHA-256 mismatch." >&2
  echo "expected: $expected_digest" >&2
  echo "actual:   $actual_digest" >&2
  exit 3
fi

openssl ts -verify \
  -data "$ARTIFACTS/payload.bin" \
  -in "$ARTIFACTS/timestamp-token.tst" \
  -token_in \
  -CAfile "$ARTIFACTS/tsa-root.pem" \
  -untrusted "$ARTIFACTS/tsa-intermediates.pem"

echo "CH-01 PASS: payload digest and RFC 3161 token verified independently."