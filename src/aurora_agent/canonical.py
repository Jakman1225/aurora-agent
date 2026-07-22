from __future__ import annotations

import hashlib
import json
from typing import Any

from .exceptions import CanonicalizationError

CANONICALIZATION_PROFILE = "aurora-agent-action-json"
CANONICALIZATION_VERSION = "0.1"
HASH_ALGORITHM = "sha256"


def _validate(value: Any, path: str = "$") -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        raise CanonicalizationError(
            f"float at {path} is not committable; normalize explicitly at the boundary"
        )
    if isinstance(value, int) or value is None:
        return value
    if isinstance(value, str):
        try:
            value.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise CanonicalizationError(
                f"string at {path} is not a valid Unicode scalar sequence"
            ) from exc
        return value
    if isinstance(value, list):
        return [_validate(item, f"{path}[{index}]") for index, item in enumerate(value)]
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise CanonicalizationError(f"non-string object key at {path}: {key!r}")
            _validate(key, f"{path}.<key>")
            result[key] = _validate(item, f"{path}.{key}")
        return result
    raise CanonicalizationError(
        f"unsupported type at {path}: {type(value).__name__}"
    )


def canonical_bytes(value: Any) -> bytes:
    checked = _validate(value)
    return json.dumps(
        checked,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def digest_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def commitment(value: Any) -> str:
    return digest_bytes(canonical_bytes(value))


def strict_json_loads(raw: bytes, *, label: str) -> Any:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CanonicalizationError(f"{label} is not UTF-8") from exc

    def reject_duplicate(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise CanonicalizationError(f"duplicate JSON key in {label}: {key!r}")
            result[key] = value
        return result

    try:
        parsed = json.loads(
            text,
            object_pairs_hook=reject_duplicate,
            parse_float=lambda value: (_ for _ in ()).throw(
                CanonicalizationError(f"float token in {label}: {value}")
            ),
            parse_constant=lambda value: (_ for _ in ()).throw(
                CanonicalizationError(f"non-finite token in {label}: {value}")
            ),
        )
    except json.JSONDecodeError as exc:
        raise CanonicalizationError(f"invalid JSON in {label}: {exc}") from exc
    return parsed
