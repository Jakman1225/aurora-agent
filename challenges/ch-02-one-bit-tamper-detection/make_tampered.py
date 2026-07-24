from __future__ import annotations

import hashlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
ARTIFACTS = HERE / "artifacts"
SOURCE = ARTIFACTS / "payload.bin"
OUTPUT = ARTIFACTS / "payload-tampered.bin"
METADATA = ARTIFACTS / "tamper-metadata.txt"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> None:
    data = bytearray(SOURCE.read_bytes())
    if not data:
        raise SystemExit("payload.bin is empty")

    offset = len(data) // 2
    original_byte = data[offset]
    data[offset] ^= 0x01
    tampered_byte = data[offset]

    OUTPUT.write_bytes(data)
    METADATA.write_text(
        "\n".join(
            [
                f"offset={offset}",
                "bit_mask=0x01",
                f"original_byte=0x{original_byte:02x}",
                f"tampered_byte=0x{tampered_byte:02x}",
                f"original_sha256={sha256(SOURCE.read_bytes())}",
                f"tampered_sha256={sha256(bytes(data))}",
                "",
            ]
        ),
        encoding="utf-8",
    )

    print(f"Wrote {OUTPUT}")
    print(f"Wrote {METADATA}")


if __name__ == "__main__":
    main()
