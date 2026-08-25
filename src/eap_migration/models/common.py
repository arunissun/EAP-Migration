"""Shared case structures and local validation helpers."""

from __future__ import annotations

import re
from enum import IntEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
FileReference = Annotated[str, Field(min_length=1)]
FileKind = Literal["evidence", "cover", "budget", "checklist"]
CompletenessProfile = Literal["draft", "strict"]


class Timeframe(IntEnum):
    YEARS = 10
    MONTHS = 20
    DAYS = 30
    HOURS = 40


class CaseModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
        str_strip_whitespace=True,
    )


def validate_email(value: str | None) -> str | None:
    if value is None:
        return None
    if value.startswith("${") and value.endswith("}"):
        return value
    if not EMAIL_RE.fullmatch(value):
        raise ValueError("must be a valid email address")
    return value


class FileSpec(CaseModel):
    path: str = Field(min_length=1)
    caption: str = Field(min_length=1, max_length=225)
    kind: FileKind | None = None
    required: bool = True


class Admin2Selection(CaseModel):
    country_iso3: list[str] = Field(default_factory=list)
    include_ids: list[int] = Field(default_factory=list)
    include_codes: list[str] = Field(default_factory=list)
    include_names: list[str] = Field(default_factory=list)
    required: bool = False

    @field_validator("country_iso3")
    @classmethod
    def normalize_iso3(cls, values: list[str]) -> list[str]:
        return [value.upper() for value in values]

    @field_validator("include_ids")
    @classmethod
    def positive_ids(cls, values: list[int]) -> list[int]:
        if any(value <= 0 for value in values):
            raise ValueError("Admin2 IDs must be positive integers")
        return values


class PartnerContact(CaseModel):
    name: str = Field(min_length=1)
    email: str
    title: str = Field(min_length=1)
    phone_number: str = ""

    _validate_email = field_validator("email")(validate_email)


class SourceInformation(CaseModel):
    source_name: str = Field(min_length=1)
    source_link: str = Field(min_length=1)


class KeyActor(CaseModel):
    partner: str = Field(min_length=1)
    description: str = Field(min_length=1)


class EarlyAction(CaseModel):
    action: str = Field(min_length=1)


class PrioritizedImpact(CaseModel):
    impact: str = Field(min_length=1)


class Indicator(CaseModel):
    title: str = Field(min_length=1)
    target: int = Field(gt=0)


class Activity(CaseModel):
    activity: str = Field(min_length=1)
    timeframe: int | None = None
    time_value: list[int] = Field(default_factory=list)
    activation_one: bool | None = None
    activation_two: bool | None = None


class PlannedOperation(CaseModel):
    sector: int = Field(gt=0)
    people_targeted: int = Field(gt=0)
    budget_per_sector: int = Field(gt=0)
    indicators: list[Indicator] = Field(default_factory=list)
    readiness_activities: list[Activity] = Field(default_factory=list)
    prepositioning_activities: list[Activity] = Field(default_factory=list)
    early_action_activities: list[Activity] = Field(default_factory=list)


class EnablingApproach(CaseModel):
    approach: int = Field(gt=0)
    budget_per_approach: int = Field(gt=0)
    indicators: list[Indicator] = Field(default_factory=list)
    readiness_activities: list[Activity] = Field(default_factory=list)
    prepositioning_activities: list[Activity] = Field(default_factory=list)
    early_action_activities: list[Activity] = Field(default_factory=list)


class ContactFields(CaseModel):
    national_society_contact_name: str = Field(min_length=1)
    national_society_contact_title: str = Field(min_length=1)
    national_society_contact_email: str
    national_society_contact_phone_number: str = ""
    dref_focal_point_name: str | None = None
    dref_focal_point_title: str | None = None
    dref_focal_point_email: str | None = None
    dref_focal_point_phone_number: str = ""

    _validate_ns_email = field_validator("national_society_contact_email")(validate_email)
    _validate_dref_email = field_validator("dref_focal_point_email")(validate_email)


def model_dict(model: BaseModel) -> dict:
    """Dump a model while keeping enum values JSON-compatible."""

    return model.model_dump(mode="json", exclude_none=True)
