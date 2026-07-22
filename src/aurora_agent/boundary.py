from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping

from .canonical import canonical_bytes
from .exceptions import BoundaryViolation

_SUPPORTED_RULES = frozenset({"json", "string", "nonblank_string", "integer", "integer_exact", "boolean"})


@dataclass(frozen=True)
class FieldRule:
    kind: str = "json"
    required: bool = True

    def __post_init__(self) -> None:
        if self.kind not in _SUPPORTED_RULES:
            raise ValueError(f"unsupported field rule: {self.kind!r}")

    @classmethod
    def json(cls, *, required: bool = True) -> "FieldRule":
        return cls("json", required)

    @classmethod
    def string(cls, *, required: bool = True) -> "FieldRule":
        return cls("string", required)

    @classmethod
    def nonblank_string(cls, *, required: bool = True) -> "FieldRule":
        return cls("nonblank_string", required)

    @classmethod
    def integer(cls, *, required: bool = True) -> "FieldRule":
        return cls("integer", required)

    @classmethod
    def integer_exact(cls, *, required: bool = True) -> "FieldRule":
        return cls("integer_exact", required)

    @classmethod
    def boolean(cls, *, required: bool = True) -> "FieldRule":
        return cls("boolean", required)

    def normalize(self, value: Any, *, field_name: str) -> Any:
        if self.kind == "json":
            canonical_bytes(value)
            return value
        if self.kind == "string":
            if not isinstance(value, str):
                raise BoundaryViolation(f"{field_name} must be a string")
            return value
        if self.kind == "nonblank_string":
            if not isinstance(value, str) or value.strip() == "":
                raise BoundaryViolation(f"{field_name} must be a nonblank string")
            return value
        if self.kind == "integer":
            if isinstance(value, bool) or not isinstance(value, int):
                raise BoundaryViolation(f"{field_name} must be an integer")
            return value
        if self.kind == "integer_exact":
            if isinstance(value, bool):
                raise BoundaryViolation(f"{field_name} must be an exact integer")
            if isinstance(value, int):
                return value
            if isinstance(value, float) and math.isfinite(value) and value.is_integer():
                return int(value)
            raise BoundaryViolation(f"{field_name} must be an exact integer")
        if self.kind == "boolean":
            if type(value) is not bool:
                raise BoundaryViolation(f"{field_name} must be a boolean")
            return value
        raise AssertionError("unreachable field rule")

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "required": self.required}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "FieldRule":
        if not isinstance(value, Mapping):
            raise BoundaryViolation("field rule must be an object")
        kind = value.get("kind")
        required = value.get("required", True)
        if not isinstance(kind, str):
            raise BoundaryViolation("field rule kind must be a string")
        if type(required) is not bool:
            raise BoundaryViolation("field rule required must be a boolean")
        return cls(kind=kind, required=required)


@dataclass(frozen=True)
class Boundary:
    boundary_id: str
    version: str
    tool: str
    fields: Mapping[str, FieldRule]
    capture_mode: str = "SDK_SELF_REPORT"
    allow_extra: bool = False

    def __post_init__(self) -> None:
        for name, value in (
            ("boundary_id", self.boundary_id),
            ("version", self.version),
            ("tool", self.tool),
            ("capture_mode", self.capture_mode),
        ):
            if not isinstance(value, str) or value == "":
                raise ValueError(f"{name} must be a non-empty string")
        if not self.fields:
            raise ValueError("fields must not be empty")
        if any(not isinstance(key, str) or key == "" for key in self.fields):
            raise ValueError("field names must be non-empty strings")
        if any(not isinstance(rule, FieldRule) for rule in self.fields.values()):
            raise TypeError("fields values must be FieldRule")

    @classmethod
    def strict(
        cls,
        *,
        boundary_id: str,
        version: str,
        tool: str,
        fields: Mapping[str, FieldRule],
        capture_mode: str = "SDK_SELF_REPORT",
    ) -> "Boundary":
        return cls(
            boundary_id=boundary_id,
            version=version,
            tool=tool,
            fields=dict(fields),
            capture_mode=capture_mode,
            allow_extra=False,
        )

    def normalize(self, *, tool: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        if tool != self.tool:
            raise BoundaryViolation(
                f"tool {tool!r} is outside boundary {self.boundary_id!r}; expected {self.tool!r}"
            )
        if not isinstance(arguments, Mapping):
            raise BoundaryViolation("arguments must be a mapping")
        if any(not isinstance(key, str) for key in arguments):
            raise BoundaryViolation("argument keys must be strings")

        actual = set(arguments)
        declared = set(self.fields)
        missing = sorted(
            name for name, rule in self.fields.items() if rule.required and name not in actual
        )
        if missing:
            raise BoundaryViolation(f"missing required argument field(s): {missing}")
        unexpected = sorted(actual - declared)
        if unexpected and not self.allow_extra:
            raise BoundaryViolation(f"unexpected argument field(s): {unexpected}")

        normalized: dict[str, Any] = {}
        for name, rule in self.fields.items():
            if name in arguments:
                normalized[name] = rule.normalize(arguments[name], field_name=name)
        if self.allow_extra:
            for name in sorted(actual - declared):
                canonical_bytes(arguments[name])
                normalized[name] = arguments[name]
        return normalized

    def subject(self, *, tool: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        return {"tool_name": tool, "arguments": self.normalize(tool=tool, arguments=arguments)}

    def to_dict(self) -> dict[str, Any]:
        return {
            "boundary_id": self.boundary_id,
            "version": self.version,
            "tool": self.tool,
            "capture_mode": self.capture_mode,
            "allow_extra": self.allow_extra,
            "fields": {name: rule.to_dict() for name, rule in sorted(self.fields.items())},
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Boundary":
        if not isinstance(value, Mapping):
            raise BoundaryViolation("boundary must be an object")
        fields_raw = value.get("fields")
        if not isinstance(fields_raw, Mapping):
            raise BoundaryViolation("boundary fields must be an object")
        boundary_id = value.get("boundary_id")
        version = value.get("version")
        tool = value.get("tool")
        capture_mode = value.get("capture_mode", "SDK_SELF_REPORT")
        allow_extra = value.get("allow_extra", False)
        for name, item in (("boundary_id", boundary_id), ("version", version), ("tool", tool), ("capture_mode", capture_mode)):
            if not isinstance(item, str):
                raise BoundaryViolation(f"boundary {name} must be a string")
        if type(allow_extra) is not bool:
            raise BoundaryViolation("boundary allow_extra must be a boolean")
        parsed_fields: dict[str, FieldRule] = {}
        for name, rule in fields_raw.items():
            if not isinstance(name, str):
                raise BoundaryViolation("boundary field names must be strings")
            parsed_fields[name] = FieldRule.from_dict(rule)
        return cls(
            boundary_id=boundary_id,
            version=version,
            tool=tool,
            capture_mode=capture_mode,
            allow_extra=allow_extra,
            fields=parsed_fields,
        )
