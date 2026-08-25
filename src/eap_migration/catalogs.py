"""Live and dated-snapshot validation for EAP reference catalogs."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from .exceptions import ValidationFailure

ENUM_KEYS = (
    "eap_eap_type",
    "eap_eap_status",
    "eap_sector",
    "eap_approach",
    "eap_timeframe",
    "eap_years_timeframe_value",
    "eap_months_timeframe_value",
    "eap_days_timeframe_value",
    "eap_hours_timeframe_value",
)

TIMEFRAME_VALUE_KEYS = {
    10: "eap_years_timeframe_value",
    20: "eap_months_timeframe_value",
    30: "eap_days_timeframe_value",
    40: "eap_hours_timeframe_value",
}


def _enum_keys(body: Mapping[str, Any], name: str) -> frozenset[int]:
    value = body.get(name)
    if not isinstance(value, list):
        raise ValidationFailure(f"Reference catalog is missing enum list '{name}'")
    keys: set[int] = set()
    for row in value:
        if not isinstance(row, Mapping):
            raise ValidationFailure(f"Reference catalog enum '{name}' contains a non-object")
        key = row.get("key")
        if isinstance(key, bool) or not isinstance(key, int):
            raise ValidationFailure(f"Reference catalog enum '{name}' contains an invalid key")
        keys.add(key)
    if not keys:
        raise ValidationFailure(f"Reference catalog enum '{name}' is empty")
    return frozenset(keys)


def _option_map(body: Mapping[str, Any], name: str) -> dict[int, frozenset[str]]:
    value = body.get(name)
    if not isinstance(value, Mapping):
        raise ValidationFailure(f"Reference options are missing map '{name}'")
    result: dict[int, frozenset[str]] = {}
    for raw_key, raw_codes in value.items():
        try:
            key = int(raw_key)
        except (TypeError, ValueError) as exc:
            raise ValidationFailure(f"Reference options '{name}' contains an invalid key") from exc
        if isinstance(raw_codes, str) or not isinstance(raw_codes, list):
            raise ValidationFailure(f"Reference options '{name}' contains an invalid value")
        codes = frozenset(code for code in raw_codes if isinstance(code, str) and code)
        if len(codes) != len(raw_codes):
            raise ValidationFailure(f"Reference options '{name}' contains an invalid AP code")
        result[key] = codes
    return result


def _catalog_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


@dataclass(frozen=True, slots=True)
class ReferenceCatalog:
    """Immutable subset of the live catalogs needed by migration validation."""

    source: str
    captured_at: str
    eap_types: frozenset[int]
    statuses: frozenset[int]
    sectors: frozenset[int]
    approaches: frozenset[int]
    timeframes: frozenset[int]
    timeframe_values: Mapping[int, frozenset[int]]
    sector_ap_codes: Mapping[int, frozenset[str]]
    approach_ap_codes: Mapping[int, frozenset[str]]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "timeframe_values",
            MappingProxyType(dict(self.timeframe_values)),
        )
        object.__setattr__(
            self,
            "sector_ap_codes",
            MappingProxyType(dict(self.sector_ap_codes)),
        )
        object.__setattr__(
            self,
            "approach_ap_codes",
            MappingProxyType(dict(self.approach_ap_codes)),
        )

    @classmethod
    def from_bodies(
        cls,
        global_enums: Mapping[str, Any],
        options: Mapping[str, Any],
        *,
        source: str,
        captured_at: str,
    ) -> ReferenceCatalog:
        sectors = _enum_keys(global_enums, "eap_sector")
        approaches = _enum_keys(global_enums, "eap_approach")
        sector_ap_codes = _option_map(options, "sector_ap_codes")
        approach_ap_codes = _option_map(options, "approach_ap_codes")
        option_sectors = frozenset(sector_ap_codes)
        option_approaches = frozenset(approach_ap_codes)
        if sectors != option_sectors:
            raise ValidationFailure(
                "Live catalog conflict for sectors: global-enums keys "
                f"{sorted(sectors)} differ from eap/options keys {sorted(option_sectors)}"
            )
        if approaches != option_approaches:
            raise ValidationFailure(
                "Live catalog conflict for approaches: global-enums keys "
                f"{sorted(approaches)} differ from eap/options keys {sorted(option_approaches)}"
            )
        return cls(
            source=source,
            captured_at=captured_at,
            eap_types=_enum_keys(global_enums, "eap_eap_type"),
            statuses=_enum_keys(global_enums, "eap_eap_status"),
            sectors=sectors,
            approaches=approaches,
            timeframes=_enum_keys(global_enums, "eap_timeframe"),
            timeframe_values={
                unit: _enum_keys(global_enums, name)
                for unit, name in TIMEFRAME_VALUE_KEYS.items()
            },
            sector_ap_codes=sector_ap_codes,
            approach_ap_codes=approach_ap_codes,
        )

    @classmethod
    def from_snapshot(cls, path: Path) -> ReferenceCatalog:
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValidationFailure(f"Reference catalog snapshot is unreadable: {path}") from exc
        if not isinstance(document, Mapping):
            raise ValidationFailure(f"Reference catalog snapshot is not an object: {path}")
        global_enums = document.get("global_enums")
        options = document.get("eap_options")
        if not isinstance(global_enums, Mapping) or not isinstance(options, Mapping):
            raise ValidationFailure(f"Reference catalog snapshot has an invalid shape: {path}")
        captured_at = document.get("captured_at")
        return cls.from_bodies(
            global_enums,
            options,
            source=f"snapshot:{path.name}",
            captured_at=captured_at if isinstance(captured_at, str) else "unknown",
        )

    def validate_case(self, case: Any) -> None:
        registration = case.registration
        if registration.eap_type not in self.eap_types:
            raise ValidationFailure(
                f"registration.eap_type={registration.eap_type} is not in the {self.source} catalog"
            )
        application = case.application
        if (
            hasattr(application, "seap_timeframe")
            and application.seap_timeframe not in self.timeframe_values[10]
        ):
            raise ValidationFailure(
                f"application.seap_timeframe={application.seap_timeframe} is not an allowed "
                "years value"
            )

        if getattr(application, "seap_lead_timeframe_unit", None) is not None:
            unit = application.seap_lead_timeframe_unit
            if unit not in {20, 30, 40} or unit not in self.timeframes:
                raise ValidationFailure(
                    f"application.seap_lead_timeframe_unit={unit} is not a supported "
                    "Simplified unit"
                )
            self._validate_value(
                "application.seap_lead_time",
                unit,
                application.seap_lead_time,
            )
            if (
                application.activation_timeframe_unit is not None
                and application.activation_timeframe_unit != 20
            ):
                raise ValidationFailure(
                    "application.activation_timeframe_unit must be Months (20)"
                )
            if application.activation_timeframe is not None:
                self._validate_value(
                    "application.activation_timeframe",
                    20,
                    application.activation_timeframe,
                )
        elif getattr(application, "lead_timeframe_unit", None) is not None:
            unit = application.lead_timeframe_unit
            if unit not in self.timeframes:
                raise ValidationFailure(
                    f"application.lead_timeframe_unit={unit} is not in the timeframe catalog"
                )

        for operation_index, operation in enumerate(application.planned_operations):
            if operation.sector not in self.sectors:
                raise ValidationFailure(
                    f"application.planned_operations.{operation_index}.sector="
                    f"{operation.sector} is not in the sector catalog"
                )
            self._validate_operation_activities(
                operation, f"application.planned_operations.{operation_index}"
            )
        for approach_index, approach in enumerate(application.enabling_approaches):
            if approach.approach not in self.approaches:
                raise ValidationFailure(
                    f"application.enabling_approaches.{approach_index}.approach="
                    f"{approach.approach} is not in the approach catalog"
                )
            self._validate_operation_activities(
                approach, f"application.enabling_approaches.{approach_index}"
            )

    def validate_payload(self, payload: Mapping[str, Any]) -> None:
        """Validate raw writable application values used by an update payload."""

        if not isinstance(payload, Mapping):
            raise ValidationFailure("Application update payload must be a JSON object")
        if "seap_timeframe" in payload and payload["seap_timeframe"] is not None:
            self._validate_value("seap_timeframe", 10, payload["seap_timeframe"])
        if "seap_lead_timeframe_unit" in payload:
            unit = payload["seap_lead_timeframe_unit"]
            if unit is not None:
                if (
                    _catalog_int(unit) is None
                    or unit not in {20, 30, 40}
                    or unit not in self.timeframes
                ):
                    raise ValidationFailure(
                        f"seap_lead_timeframe_unit={unit} is not a supported Simplified unit"
                    )
                if payload.get("seap_lead_time") is not None:
                    self._validate_value(
                        "seap_lead_time", unit, payload["seap_lead_time"]
                    )
        if "activation_timeframe_unit" in payload:
            unit = payload["activation_timeframe_unit"]
            if unit is not None and (_catalog_int(unit) is None or unit != 20):
                raise ValidationFailure(
                    "activation_timeframe_unit must be Months (20)"
                )
            if payload.get("activation_timeframe") is not None:
                self._validate_value(
                    "activation_timeframe", 20, payload["activation_timeframe"]
                )
        if "lead_timeframe_unit" in payload and payload["lead_timeframe_unit"] is not None:
            if (
                _catalog_int(payload["lead_timeframe_unit"]) is None
                or payload["lead_timeframe_unit"] not in self.timeframes
            ):
                raise ValidationFailure(
                    f"lead_timeframe_unit={payload['lead_timeframe_unit']} "
                    "is not in the timeframe catalog"
                )
        self._validate_payload_operations(payload.get("planned_operations"), "planned_operations")
        self._validate_payload_operations(
            payload.get("enabling_approaches"), "enabling_approaches", approach=True
        )

    def _validate_payload_operations(
        self, value: Any, path: str, *, approach: bool = False
    ) -> None:
        if value is None:
            return
        if not isinstance(value, list):
            raise ValidationFailure(f"{path} must be a list")
        for index, operation in enumerate(value):
            if not isinstance(operation, Mapping):
                raise ValidationFailure(f"{path}.{index} must be an object")
            key = "approach" if approach else "sector"
            catalog = self.approaches if approach else self.sectors
            if key in operation and (
                _catalog_int(operation[key]) is None or operation[key] not in catalog
            ):
                raise ValidationFailure(
                    f"{path}.{index}.{key}={operation[key]} is not in the catalog"
                )
            for field in (
                "readiness_activities",
                "prepositioning_activities",
                "early_action_activities",
            ):
                activities = operation.get(field)
                if activities is None:
                    continue
                if not isinstance(activities, list):
                    raise ValidationFailure(f"{path}.{index}.{field} must be a list")
                for activity_index, activity in enumerate(activities):
                    activity_path = f"{path}.{index}.{field}.{activity_index}"
                    if not isinstance(activity, Mapping):
                        raise ValidationFailure(f"{activity_path} must be an object")
                    timeframe = activity.get("timeframe")
                    time_values = activity.get("time_value", [])
                    if timeframe is not None:
                        if (
                            _catalog_int(timeframe) is None
                            or timeframe not in self.timeframes
                        ):
                            raise ValidationFailure(
                                f"{activity_path}.timeframe={timeframe} is not in the catalog"
                            )
                        if not isinstance(time_values, list):
                            raise ValidationFailure(f"{activity_path}.time_value must be a list")
                        for time_value in time_values:
                            if (
                                _catalog_int(time_value) is None
                                or time_value not in self.timeframe_values[timeframe]
                            ):
                                raise ValidationFailure(
                                    f"{activity_path}.time_value contains {time_value}, "
                                    f"which is not allowed for timeframe {timeframe}"
                                )
                    elif time_values:
                        raise ValidationFailure(
                            f"{activity_path}.time_value requires a timeframe unit"
                        )

    def _validate_operation_activities(self, operation: Any, path: str) -> None:
        for field in (
            "readiness_activities",
            "prepositioning_activities",
            "early_action_activities",
        ):
            for index, activity in enumerate(getattr(operation, field)):
                if activity.timeframe is not None:
                    if activity.timeframe not in self.timeframes:
                        raise ValidationFailure(
                            f"{path}.{field}.{index}.timeframe={activity.timeframe} "
                            "is not in the timeframe catalog"
                        )
                    for value in activity.time_value:
                        if value not in self.timeframe_values[activity.timeframe]:
                            raise ValidationFailure(
                                f"{path}.{field}.{index}.time_value contains {value}, "
                                f"which is not allowed for timeframe {activity.timeframe}"
                            )
                elif activity.time_value:
                    raise ValidationFailure(
                        f"{path}.{field}.{index}.time_value requires a timeframe unit"
                    )

    def _validate_value(self, path: str, unit: int, value: int) -> None:
        if _catalog_int(value) is None:
            raise ValidationFailure(f"{path} must be an integer catalog value")
        if value not in self.timeframe_values[unit]:
            raise ValidationFailure(
                f"{path}={value} is not allowed for timeframe unit {unit}"
            )

    def summary(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "captured_at": self.captured_at,
            "eap_types": sorted(self.eap_types),
            "statuses": sorted(self.statuses),
            "sectors": sorted(self.sectors),
            "approaches": sorted(self.approaches),
            "timeframes": sorted(self.timeframes),
            "timeframe_values": {
                str(unit): sorted(values) for unit, values in self.timeframe_values.items()
            },
        }


def fetch_reference_catalog(client: Any) -> ReferenceCatalog:
    global_enums = client.get("global-enums/")
    options = client.get("eap/options/")
    if not isinstance(global_enums, Mapping) or not isinstance(options, Mapping):
        raise ValidationFailure("Live reference endpoints returned unexpected JSON")
    return ReferenceCatalog.from_bodies(
        global_enums,
        options,
        source="live",
        captured_at="live",
    )
