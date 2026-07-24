# CH-01 — Independent RFC 3161 Verification

## Objective

Verify an AURORA timestamp token without using AURORA code.

## Required artifacts

Place the following files in `artifacts/`:

- `payload.bin` — exact bytes whose digest was timestamped;
- `timestamp-token.tst` — DER-encoded RFC 3161/CMS timestamp token;
- `tsa-root.pem` — trusted TSA root certificate;
- `tsa-intermediates.pem` — required intermediate certificate chain;
- `expected-payload-sha256.txt` — lowercase SHA-256 digest followed by a newline;
- `MANIFEST.sha256` — SHA-256 checksums for all published challenge artifacts.

## Run

```bash
./verify.sh
```

## Expected result

The script must exit with status `0` and print:

```text
CH-01 PASS: payload digest and RFC 3161 token verified independently.
```

## Manual OpenSSL verification

```bash
openssl dgst -sha256 artifacts/payload.bin

openssl ts -verify \
  -data artifacts/payload.bin \
  -in artifacts/timestamp-token.tst \
  -token_in \
  -CAfile artifacts/tsa-root.pem \
  -untrusted artifacts/tsa-intermediates.pem
```

## Interpretation boundary

PASS means the token verifies against the exact supplied payload bytes and supplied TSA chain. It does not prove capture completeness, legal authority, event truth, or external-world effect.
