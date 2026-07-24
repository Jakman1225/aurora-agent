#!/usr/bin/env bash
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ARTIFACTS="$HERE/artifacts"

required=(
  payload.bin
  payload-tampered.bin
  timestamp-token.tst
  tsa-root.pem
  tsa-intermediates.pem
  expected-payload-sha256.txt
  tamper-metadata.txt
)

for name in "${required[@]}"; do
  if [[ ! -s "$ARTIFACTS/$name" ]]; then
    echo "Missing or empty artifact: artifacts/$name" >&2
    exit 2
  fi
done

original_digest="$(sha256sum "$ARTIFACTS/payload.bin" | awk '{print $1}')"
expected_digest="$(tr -d '[:space:]' < "$ARTIFACTS/expected-payload-sha256.txt")"
tampered_digest="$(sha256sum "$ARTIFACTS/payload-tampered.bin" | awk '{print $1}')"

if [[ "$original_digest" != "$expected_digest" ]]; then
  echo "Original payload digest does not match the published expected digest." >&2
  exit 3
fi

if [[ "$tampered_digest" == "$original_digest" ]]; then
  echo "Tampered payload digest unexpectedly matches the original digest." >&2
  exit 4
fi

openssl ts -verify \
  -data "$ARTIFACTS/payload.bin" \
  -in "$ARTIFACTS/timestamp-token.tst" \
  -token_in \
  -CAfile "$ARTIFACTS/tsa-root.pem" \
  -untrusted "$ARTIFACTS/tsa-intermediates.pem"

set +e
openssl ts -verify \
  -data "$ARTIFACTS/payload-tampered.bin" \
  -in "$ARTIFACTS/timestamp-token.tst" \
  -token_in \
  -CAfile "$ARTIFACTS/tsa-root.pem" \
  -untrusted "$ARTIFACTS/tsa-intermediates.pem" \
  >"$HERE/tampered-verification.stdout.txt" \
  2>"$HERE/tampered-verification.stderr.txt"
tampered_status=$?
set -e

if [[ $tampered_status -eq 0 ]]; then
  echo "Tampered payload unexpectedly verified against the original token." >&2
  exit 5
fi

echo "CH-02 PASS: original verified; one-bit-modified payload was rejected."
