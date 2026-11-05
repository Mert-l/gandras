"""Pydantic schemas for VLM structured output and results."""
from typing import Literal

from pydantic import BaseModel, Field

Certainty = Literal["low", "medium", "high"]


class VisualObservation(BaseModel):
    feature: str = Field(description="What/where, e.g. 'region 2, south pit wall'")
    observation: str = Field(description="What is visibly different, described only")
    possible_explanation: str | None = Field(description="Plausible explanation, or null")
    certainty: Certainty


class VisualAnalysis(BaseModel):
    summary: str
    observations: list[VisualObservation] = Field(max_length=6, description="At most 6; group similar regions")
    alternative_explanations: list[str]
    limitations: list[str]
    additional_evidence_needed: list[str]
    changes_consistent_with_measurements: Literal["yes", "partly", "no", "unclear"]


class VLMResult(BaseModel):
    status: Literal["ok", "unavailable"]
    model: str
    analysis: VisualAnalysis | None = None
    error: str | None = None
    attempts: int
    duration_s: float
