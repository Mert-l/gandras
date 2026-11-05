"""Surface water (WaterCheck): MNDWI water maps on both dates; new and lost water areas."""
from __future__ import annotations

import numpy as np
from pydantic import BaseModel

from app.analysis import visualize as viz
from app.analysis.change_detection import clean_mask, regions
from app.analysis.measurements import mask_area_ha
from app.indicators.base import Indicator, grid_area_ha, output, pick_direction


class WaterThresholds(BaseModel):
    mndwi_min: float = 0.0       # MNDWI > 0 is the conventional open-water split (Xu 2006); tune per site
    min_region_ha: float = 0.5
    open_px: int = 1


def mndwi(b: dict) -> np.ndarray:
    g, s = b["B03"], b["B11"]
    with np.errstate(divide="ignore", invalid="ignore"):
        out = (g - s) / (g + s)
    out[~np.isfinite(out)] = np.nan
    return out.astype(np.float32)


def _analyze(before, after, valid, grid, direction="any", th: WaterThresholds | None = None):
    th = th or WaterThresholds()
    mb, ma = mndwi(before), mndwi(after)
    comp = valid & np.isfinite(mb) & np.isfinite(ma)
    wb, wa = comp & (mb > th.mndwi_min), comp & (ma > th.mndwi_min)
    gained, g_regs = regions(clean_mask(comp & ~wb & wa, th.open_px), grid, th.min_region_ha, layers={"mndwi_after": ma})
    lost, l_regs = regions(clean_mask(comp & wb & ~wa, th.open_px), grid, th.min_region_ha, layers={"mndwi_before": mb})
    ev = pick_direction(direction, gained, lost)
    regs = g_regs if direction == "increase" else l_regs if direction == "decrease" else sorted(
        [dict(r, kind="new_water") for r in g_regs] + [dict(r, kind="lost_water") for r in l_regs], key=lambda r: -r["area_ha"])
    for i, r in enumerate(regs, 1):
        r["id"] = i
    wb_ha, wa_ha = mask_area_ha(wb, grid), mask_area_ha(wa, grid)
    r = {"analysis_type": "surface_water_change", "method": "mndwi_threshold_both_dates_and_spatial_filter",
         "aoi_area_ha": grid_area_ha(grid), "valid_area_ha": round(mask_area_ha(comp, grid), 2),
         "water_before_ha": round(wb_ha, 2), "water_after_ha": round(wa_ha, 2), "net_water_change_ha": round(wa_ha - wb_ha, 2),
         "new_water_ha": round(mask_area_ha(gained, grid), 2), "lost_water_ha": round(mask_area_ha(lost, grid), 2),
         "evidence_ha": round(mask_area_ha(ev, grid), 2), "direction": direction,
         "regions_detected": len(regs), "regions": regs[:50], "thresholds": th.model_dump(),
         "excluded": ["SCL invalid on either date", "regions < min_region_ha"]}
    other = (gained | lost) & ~ev
    prev = {"Water before": viz.binary_rgb(wb, comp), "Water after": viz.binary_rgb(wa, comp),
            "MNDWI change": viz.delta_rgb(np.where(comp, ma - mb, np.nan), span=0.8)}
    legend = {"red": {"increase": "new water (evidence)", "decrease": "lost water (evidence)", "any": "water change (evidence)"}[direction]}
    if other.any():
        legend["amber"] = "water change in the other direction"
    return output(r, ev, comp, other=other, previews=prev, legend=legend)


def _observed(r, d):
    return [f"Open water (MNDWI > {r['thresholds']['mndwi_min']}) covered {r['water_before_ha']:.1f} ha before and "
            f"{r['water_after_ha']:.1f} ha after (net {r['net_water_change_ha']:+.1f} ha) within the valid comparison area.",
            f"New water appeared over {r['new_water_ha']:.1f} ha and water disappeared from {r['lost_water_ha']:.1f} ha "
            f"(regions ≥ {r['thresholds']['min_region_ha']} ha)."]


def _inferred(r, d):
    return ["Water-extent changes can reflect seasonal levels, rainfall, reservoir operation, irrigation or flooding; "
            "two dates cannot separate a trend from normal variation."]


WATER = Indicator(
    id="water", name="Surface water", short="WaterCheck",
    description="Maps open water on both dates (MNDWI) and measures water that appeared or disappeared.",
    directions=["increase", "decrease", "any"], default_direction="any", evidence_label="surface-water change",
    keywords=("water", "lake", "reservoir", "river", "flood", "dam", "pond", "wetland", "dried", "drought", "shoreline",
              "lagoon", "irrigation", "inundat"),
    example_claims=["The reservoir has not shrunk since 2019.", "The lake dried up between 2020 and 2023.",
                    "No flooding occurred in this area in 2022."],
    analyze=_analyze, observed=_observed, inferred=_inferred,
    not_established=["The cause of the water change (climate, extraction, dam operation).",
                     "Water volume or depth (only surface extent is measured)."],
    vlm_guidance=("Water: compare dark/blue open-water extents; note shorelines, exposed lake beds, new ponds or flooded "
                  "fields, versus cloud shadow, dark rock or burn scars that can look like water."),
    significant_ha=5.0, negligible_ha=1.0, confidence_cap="high",
)
