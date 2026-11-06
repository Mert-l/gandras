"""API request models with input validation."""
from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.config import get_settings


class CustomLocation(BaseModel):
    latitude: float = Field(ge=-84, le=84)
    longitude: float = Field(ge=-180, le=180)
    width_km: float = Field(default=5, gt=0.5)
    before_date: date
    after_date: date

    @model_validator(mode="after")
    def _check(self) -> "CustomLocation":
        max_km2 = get_settings().max_aoi_km2
        if self.width_km ** 2 > max_km2:
            raise ValueError(f"AOI of {self.width_km ** 2:.0f} km² exceeds the {max_km2:.0f} km² limit")
        if self.before_date < date(2017, 1, 1):
            raise ValueError("before_date must be 2017 or later (Sentinel-2 L2A coverage)")
        if self.after_date <= self.before_date:
            raise ValueError("after_date must be later than before_date")
        if self.after_date > date.today():
            raise ValueError("after_date cannot be in the future")
        return self


class InvestigationOptions(BaseModel):
    max_agent_steps: int | None = Field(default=None, ge=3, le=15)
    allow_additional_imagery: bool = True
    # Registry id from GET /models; only ids the server knows are accepted (no client-supplied URLs).
    model: str | None = Field(default=None, max_length=200, pattern=r"^[A-Za-z0-9._:/@-]+$")


class CreateInvestigation(BaseModel):
    claim: str = Field(min_length=5, max_length=500)
    indicator: str = Field(default="auto", max_length=40, pattern=r"^[a-z_]+$")
    mode: Literal["mining", "forest"] | None = None  # legacy alias for indicator
    case_id: str | None = Field(default=None, max_length=64, pattern=r"^[a-z0-9_]+$")
    custom: CustomLocation | None = None
    options: InvestigationOptions = Field(default_factory=InvestigationOptions)

    @model_validator(mode="after")
    def _resolve_indicator(self) -> "CreateInvestigation":
        if self.indicator == "auto" and self.mode is None:
            return self
        from app.indicators import LEGACY_MODES, REGISTRY
        if self.mode and self.indicator == "auto":
            self.indicator = LEGACY_MODES[self.mode]
        if self.indicator != "auto" and self.indicator not in REGISTRY:
            raise ValueError(f"Unknown indicator '{self.indicator}'. Use 'auto' or one of: {', '.join(REGISTRY)}")
        return self

    @model_validator(mode="after")
    def _one_location(self) -> "CreateInvestigation":
        if self.case_id and self.custom:
            raise ValueError("Provide either case_id or custom, not both")
        return self


class ConfirmLocation(BaseModel):
    candidate_id: str | None = Field(default=None, max_length=64)
    custom: CustomLocation | None = None

    @model_validator(mode="after")
    def _one(self) -> "ConfirmLocation":
        if bool(self.candidate_id) == bool(self.custom):
            raise ValueError("Provide exactly one candidate_id or custom location")
        return self
