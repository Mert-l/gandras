"""Vegetation cover (general): NDVI loss or gain of any vegetation (crops, grass, shrubs, trees)."""
from __future__ import annotations

import numpy as np

from app.analysis import visualize as viz
from app.analysis.change_detection import clean_mask, regions
from app.analysis.measurements import mask_area_ha
from app.analysis.vegetation import VegetationThresholds, ndvi
from app.indicators.base import Indicator, grid_area_ha, output, pick_direction


def _analyze(before, after, valid, grid, direction="decrease"):
    th = VegetationThresholds()
    nb, na = ndvi(before["B08"], before["B04"]), ndvi(after["B08"], after["B04"])
    water = (before["SCL"] == 6) | (after["SCL"] == 6)
    comp = valid & ~water & np.isfinite(nb) & np.isfinite(na)
    d = np.where(comp, na - nb, np.nan)
    loss, l_regs = regions(clean_mask(comp & (nb > th.ndvi_before_min) & (na < th.ndvi_after_max) & (d < th.delta_ndvi_max), 1),
                           grid, th.min_region_ha, layers={"delta_ndvi": d})
    gain, g_regs = regions(clean_mask(comp & (nb < th.ndvi_after_max) & (na > th.ndvi_before_min) & (d > -th.delta_ndvi_max), 1),
                           grid, th.min_region_ha, layers={"delta_ndvi": d})
    ev = pick_direction(direction, gain, loss)
    regs = g_regs if direction == "increase" else l_regs if direction == "decrease" else sorted(g_regs + l_regs, key=lambda r: -r["area_ha"])
    for i, r in enumerate(regs, 1):
        r["id"] = i
    res = {"analysis_type": "vegetation_cover_change", "method": "ndvi_threshold_loss_and_gain_with_spatial_filter",
           "aoi_area_ha": grid_area_ha(grid), "valid_area_ha": round(mask_area_ha(comp, grid), 2),
           "vegetation_loss_ha": round(mask_area_ha(loss, grid), 2), "vegetation_gain_ha": round(mask_area_ha(gain, grid), 2),
           "evidence_ha": round(mask_area_ha(ev, grid), 2), "direction": direction,
           "mean_ndvi_before": round(float(np.nanmean(nb[comp])), 4) if comp.any() else None,
           "mean_ndvi_after": round(float(np.nanmean(na[comp])), 4) if comp.any() else None,
           "regions_detected": len(regs), "regions": regs[:50],
           "thresholds": {"ndvi_high": th.ndvi_before_min, "ndvi_low": th.ndvi_after_max, "delta_min": -th.delta_ndvi_max,
                          "min_region_ha": th.min_region_ha}}
    other = (loss | gain) & ~ev
    legend = {"red": {"increase": "vegetation gain (evidence)", "decrease": "vegetation loss (evidence)", "any": "vegetation change (evidence)"}[direction]}
    if other.any():
        legend["amber"] = "vegetation change in the other direction"
    return output(res, ev, comp, other=other, legend=legend,
                  previews={"NDVI before": viz.ndvi_rgb(nb), "NDVI after": viz.ndvi_rgb(na), "NDVI change": viz.delta_rgb(d)})


def _observed(r, d):
    return [f"Vegetation index (NDVI) loss covered {r['vegetation_loss_ha']:.1f} ha and gain {r['vegetation_gain_ha']:.1f} ha "
            f"(regions ≥ {r['thresholds']['min_region_ha']} ha); mean NDVI {r['mean_ndvi_before']} → {r['mean_ndvi_after']}."]


def _inferred(r, d):
    return ["NDVI changes can reflect planting, harvest, growth stage, irrigation, drought or management; two dates "
            "cannot separate lasting change from season-to-season variation."]


VEGETATION = Indicator(
    id="vegetation_cover", name="Vegetation cover", short="GreenCheck",
    description="Measures loss or gain of any vegetation (NDVI), e.g. restoration, replanting, crops, green space.",
    directions=["increase", "decrease"], default_direction="decrease", evidence_label="vegetation-cover change",
    keywords=("vegetation", "green", "grass", "crop", "farm", "field", "plant", "reforest", "restor", "regrow", "replant",
              "park", "meadow", "shrub", "agricultur", "orchard", "mangrove"),
    example_claims=["The restoration project has re-vegetated this area since 2019.", "The park's green space has not shrunk since 2020."],
    analyze=_analyze, observed=_observed, inferred=_inferred,
    not_established=["Species, health or survival of planted vegetation.", "Who carried out any planting or removal."],
    vlm_guidance=("Vegetation: compare green cover; note new plantings, cleared fields, harvest patterns, versus seasonal "
                  "greening/browning, irrigation or haze."),
    significant_ha=5.0, negligible_ha=1.0, confidence_cap="medium",
)
