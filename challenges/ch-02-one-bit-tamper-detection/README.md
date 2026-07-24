# CH-02 — One-Bit Tamper Detection

## Objective

Demonstrate that changing one bit in the original payload causes verification against the unchanged RFC 3161 timestamp token to fail.

## Required artifacts

Copy the same five cryptographic inputs used in CH-01 into `artifacts/`:

- `payload.bin`;
- `timestamp-token.tst`;
- `tsa-root.pem`;
- `tsa-intermediates.pem`;
- `expected-payload-sha256.txt`.

Then generate the tampered payload:

```bash
python make_tampered.py
```

The generator flips exactly one bit at a deterministic offset and writes:

```text
artifacts/payload-tampered.bin
artifacts/tamper-metadata.txt
```

## Run

```bash
./verify.sh
```

## Expected result

The original payload must verify. The one-bit-modified payload must fail against the same token. The script exits with status `0` only when both conditions hold.

```text
CH-02 PASS: original verified; one-bit-modified payload was rejected.
```

## Interpretation boundary

This challenge demonstrates cryptographic change detection for the supplied bytes. It does not prove that every relevant input was captured or that the original payload was factually correct.
