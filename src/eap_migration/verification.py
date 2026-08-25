"""Normalized field comparison and compact verification reports."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

SET_FIELDS = {
    "admin2",
    "partners",
    "users",
}


@dataclass(slots=True)
class VerificationReport:
    ok: bool
    differences: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "differences": self.differences}


def _normalize(value: Any, path: str = "") -> Any:
    if isinstance(value, dict):
        return {key: _normalize(value[key], f"{path}.{key}".strip(".")) for key in sorted(value)}
    if isinstance(value, list):
        normalized = [_normalize(item, path) for item in value]
        field = path.rsplit(".", 1)[-1]
        if field in SET_FIELDS:
            return sorted(normalized, key=lambda item: repr(item))
        return normalized
    return value


def compare_payload(expected: dict[str, Any], actual: dict[str, Any]) -> VerificationReport:
    differences: list[dict[str, Any]] = []
    expected_normalized = _normalize(expected)
    actual_normalized = _normalize(actual)

    def walk(left: Any, right: Any, path: str) -> None:
        if isinstance(left, dict) and isinstance(right, dict):
            for key in sorted(set(left) | set(right)):
                child_path = f"{path}.{key}".strip(".")
                if key not in left:
                    differences.append(
                        {"path": child_path, "expected": "<absent>", "actual": right[key]}
                    )
                elif key not in right:
                    differences.append(
                        {"path": child_path, "expected": left[key], "actual": "<absent>"}
                    )
                else:
                    walk(left[key], right[key], child_path)
            return
        if isinstance(left, list):
            if not isinstance(right, list):
                differences.append({"path": path, "expected": left, "actual": right})
                return
            if len(left) != len(right):
                for index in range(len(left), len(right)):
                    child_path = f"{path}.{index}".strip(".")
                    differences.append(
                        {"path": child_path, "expected": "<absent>", "actual": right[index]}
                    )
            for index, left_value in enumerate(left):
                child_path = f"{path}.{index}".strip(".")
                if index >= len(right):
                    differences.append(
                        {"path": child_path, "expected": left_value, "actual": "<absent>"}
                    )
                else:
                    walk(left_value, right[index], child_path)
            return
        if left != right:
            differences.append({"path": path, "expected": left, "actual": right})

    walk(expected_normalized, actual_normalized, "")
    return VerificationReport(ok=not differences, differences=differences)


def compare_expected_payload(
    expected: dict[str, Any], actual: dict[str, Any]
) -> VerificationReport:
    """Compare expected writable fields while ignoring server-owned extra fields."""

    differences: list[dict[str, Any]] = []
    expected_normalized = _normalize(expected)
    actual_normalized = _normalize(actual)

    def walk(left: Any, right: Any, path: str) -> None:
        if isinstance(left, dict):
            if not isinstance(right, dict):
                differences.append({"path": path, "expected": left, "actual": right})
                return
            for key, left_value in left.items():
                child_path = f"{path}.{key}".strip(".")
                if key not in right:
                    differences.append(
                        {"path": child_path, "expected": left_value, "actual": "<absent>"}
                    )
                else:
                    walk(left_value, right[key], child_path)
            return
        if isinstance(left, list):
            if not isinstance(right, list):
                differences.append({"path": path, "expected": left, "actual": right})
                return
            if len(left) != len(right):
                for index in range(len(left), len(right)):
                    child_path = f"{path}.{index}".strip(".")
                    differences.append(
                        {"path": child_path, "expected": "<absent>", "actual": right[index]}
                    )
            for index, left_value in enumerate(left):
                child_path = f"{path}.{index}".strip(".")
                if index >= len(right):
                    differences.append(
                        {"path": child_path, "expected": left_value, "actual": "<absent>"}
                    )
                else:
                    walk(left_value, right[index], child_path)
            return
        if left != right:
            differences.append({"path": path, "expected": left, "actual": right})

    walk(expected_normalized, actual_normalized, "")
    return VerificationReport(ok=not differences, differences=differences)
