"""Indicator interface: one physical signal Sentinel-2 can measure, plus how to judge claims about it.

An indicator owns: the bands it needs, its measurement (deterministic), which change directions a
claim can assert, the evidence-area thresholds, whether persistence must be confirmed, a confidence
cap, and the wording for reports and the VLM. The agent, verification engine and UI are generic.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Literal

import numpy as np

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.satellite.aoi import AnalysisGrid

Direction = Literal["increase", "decrease", "any"]
STANDARD_BANDS = ["B02", "B03", "B04", "B08", "B11", "SCL"]


@dataclass
class Indicator:
    id: str
    name: str
    short: str
    description: str
    directions: list[str]
    default_direction: str
    evidence_label: str                      # e.g. "probable forest clearing"
    keywords: tuple[str, ...]
    example_claims: list[str]
    analyze: Callable[..., dict]             # (before, after, valid, grid, direction) -> AnalysisOutput
    observed: Callable[[dict, str], list[str]]
    inferred: Callable[[dict, str], list[str]]
    not_established: list[str]
    vlm_guidance: str
    significant_ha: float
    negligible_ha: float
    requires_persistence: bool = False
    confidence_cap: str = "high"
    is_fallback: bool = False
    bands: list[str] = field(default_factory=lambda: list(STANDARD_BANDS))
    notes: Callable[[dict, str], list[str]] = lambda r, d: []
    min_region_ha: float = 0.5

    def public(self) -> dict:
        return {"id": self.id, "name": self.name, "short": self.short, "description": self.description,
                "directions": self.directions, "evidence_label": self.evidence_label,
                "example_claims": self.example_claims, "is_fallback": self.is_fallback,
                "significant_ha": self.significant_ha, "negligible_ha": self.negligible_ha,
                "requires_persistence": self.requires_persistence, "confidence_cap": self.confidence_cap}

    def info(self) -> dict:
        return {"id": self.id, "name": self.name, "short": self.short, "evidence_label": self.evidence_label,
                "is_fallback": self.is_fallback}


def output(result: dict, evidence: np.ndarray, valid: np.ndarray, other: np.ndarray | None = None,
           previews: dict[str, np.ndarray] | None = None, legend: dict | None = None) -> dict:
    """Standard analysis output. `result` must contain evidence_ha and regions (evidence regions)."""
    assert "evidence_ha" in result and "regions" in result
    result.setdefault("overlay_legend", legend or {"red": result.get("evidence_label", "evidence")})
    return {"result": result, "masks": {"evidence": evidence, "other": other, "valid": valid},
            "previews": previews or {}}


def pick_direction(direction: str, inc: np.ndarray, dec: np.ndarray) -> np.ndarray:
    return inc if direction == "increase" else dec if direction == "decrease" else (inc | dec)


def grid_area_ha(grid: AnalysisGrid) -> float:
    return round(grid.width * grid.height * grid.pixel_area_m2 / 10_000, 2)
