"""Pydantic models for migration cases and API payload inputs."""

from .full import FullCase, FullEapApplication
from .registration import RegistrationCase
from .simplified import SimplifiedCase, SimplifiedEapApplication

EapCase = SimplifiedCase | FullCase

__all__ = [
    "EapCase",
    "FullCase",
    "FullEapApplication",
    "RegistrationCase",
    "SimplifiedCase",
    "SimplifiedEapApplication",
]
