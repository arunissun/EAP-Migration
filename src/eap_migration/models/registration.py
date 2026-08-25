"""EAP registration input model."""

from __future__ import annotations

from datetime import date

from pydantic import Field, field_validator

from .common import CaseModel, validate_email


class RegistrationCase(CaseModel):
    country: int = Field(gt=0)
    national_society: int = Field(gt=0)
    disaster_type: int = Field(gt=0)
    disaster_sub_type: str = ""
    eap_type: int
    expected_submission_time: date
    partners: list[int] = Field(default_factory=list)
    users: list[int] = Field(default_factory=list)
    national_society_contact_name: str = Field(min_length=1)
    national_society_contact_title: str = Field(min_length=1)
    national_society_contact_email: str
    national_society_contact_phone_number: str = ""
    ifrc_contact_name: str = Field(min_length=1)
    ifrc_contact_title: str = Field(min_length=1)
    ifrc_contact_email: str
    ifrc_contact_phone_number: str = ""
    dref_focal_point_name: str = Field(min_length=1)
    dref_focal_point_title: str = Field(min_length=1)
    dref_focal_point_email: str
    dref_focal_point_phone_number: str = ""

    _validate_ns_email = field_validator("national_society_contact_email")(validate_email)
    _validate_ifrc_email = field_validator("ifrc_contact_email")(validate_email)
    _validate_dref_email = field_validator("dref_focal_point_email")(validate_email)

    @field_validator("eap_type")
    @classmethod
    def supported_type(cls, value: int) -> int:
        if value not in {10, 20}:
            raise ValueError("eap_type must be 10 (Full) or 20 (Simplified)")
        return value

    @field_validator("partners", "users")
    @classmethod
    def positive_foreign_keys(cls, values: list[int]) -> list[int]:
        if any(value <= 0 for value in values):
            raise ValueError("foreign-key IDs must be positive integers")
        return values

    def to_payload(self) -> dict:
        payload = self.model_dump(mode="json")
        payload["expected_submission_time"] = self.expected_submission_time.isoformat()
        return payload
