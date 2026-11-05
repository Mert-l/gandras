"""Generic fallback: general multispectral surface change for claims no specific indicator covers.

It measures where the surface changed, but cannot tell whether that change is what the claim is
about, so it can only ever return "supported" (for a no-change claim with negligible change, at most
low confidence) or "inconclusive" — never "contradicted".
"""
from __future__ import annotations

from app.indicators.surface import _analyze
from app.indicators.base import Indicator


def _observed(r, d):
    return [f"General multispectral surface change above a locally derived threshold covered {r['evidence_ha']:.1f} ha in "
            f"{r['regions_detected']} regions ≥ {r['thresholds']['min_region_ha']} ha."]


def _inferred(r, d):
    return ["No specific indicator matched the claim, so the system cannot tell whether these changes relate to it."]


GENERIC = Indicator(
    id="generic_change", name="General surface change", short="Generic",
    description="Fallback for claims no specific indicator covers: reports where the surface changed. "
                "Verdicts are limited to supported (no change) or inconclusive.",
    directions=["any"], default_direction="any", evidence_label="general surface change",
    keywords=(), example_claims=["Nothing has changed at this site since 2020."],
    analyze=_analyze, observed=_observed, inferred=_inferred,
    not_established=["Whether any measured change is the change the claim refers to.", "Cause, actor or legality of any change."],
    vlm_guidance=("General: describe the most visible differences (new structures, cleared or flooded ground, vegetation "
                  "changes) and what else could explain them."),
    significant_ha=5.0, negligible_ha=1.0, confidence_cap="low", is_fallback=True, min_region_ha=1.0,
)
