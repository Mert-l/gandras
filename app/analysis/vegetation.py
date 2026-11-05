"""ForestCheck: NDVI-based candidate vegetation-loss detection.

Thresholds are configurable starting heuristics, not validated universal deforestation criteria.
Vegetation loss is reported as an observation; it is not automatically deforestation.
"""
from __future__ import annotations

import numpy as np
from pydantic import BaseModel

from app.analysis.change_detection import clean_mask, regions
from app.analysis.measurements import mask_area_ha, safe_fraction
from app.satellite.aoi import AnalysisGrid

SCL_WATER = 6
SCL_VEGETATION = 4


class VegetationThresholds(BaseModel):
    ndvi_before_min: float = 0.45
    ndvi_after_max: float = 0.25
    delta_ndvi_max: float = -0.20
    min_region_ha: float = 0.5
    open_px: int = 1
    # Stricter "forest-like before" signal: dense canopy is high-NDVI and dark in SWIR, which
    # separates it from green pasture/crops (measured: forest NDVI~0.83/B11~0.13 vs pasture
    # NDVI~0.58/B11~0.25 at a test site at -9.35,-64.45, Jul 2020). Starting heuristic, not a land-cover product.
    forest_ndvi_min: float = 0.70
    forest_swir_max: float = 0.18
    bare_bsi_rise_min: float = 0.05


def ndvi(nir: np.ndarray, red: np.ndarray) -> np.ndarray:
    """(NIR - RED) / (NIR + RED); NaN where inputs are NaN or the denominator is ~0."""
    den = nir + red
    with np.errstate(divide="ignore", invalid="ignore"):
        out = (nir - red) / den
    out[~np.isfinite(out) | (np.abs(den) < 1e-6)] = np.nan
    return out.astype(np.float32)


def analyze_vegetation_change(before: dict[str, np.ndarray], after: dict[str, np.ndarray],
                              valid: np.ndarray, grid: AnalysisGrid,
                              th: VegetationThresholds | None = None) -> dict:
    th = th or VegetationThresholds()
    nb, na = ndvi(before["B08"], before["B04"]), ndvi(after["B08"], after["B04"])
    water = (before["SCL"] == SCL_WATER) | (after["SCL"] == SCL_WATER)
    comp = valid & ~water & np.isfinite(nb) & np.isfinite(na)
    delta = np.where(comp, na - nb, np.nan).astype(np.float32)

    raw = comp & (nb > th.ndvi_before_min) & (na < th.ndvi_after_max) & (delta < th.delta_ndvi_max)
    from app.analysis.mining import bsi  # local import avoids a cycle
    d_bsi = np.where(comp, bsi(after) - bsi(before), np.nan).astype(np.float32)
    forest_before = comp & (nb >= th.forest_ndvi_min) & (before["B11"] < th.forest_swir_max)
    kept, regs = regions(clean_mask(raw, th.open_px), grid, th.min_region_ha,
                         layers={"delta_ndvi": delta, "ndvi_before": nb, "ndvi_after": na,
                                 "delta_bsi": d_bsi, "forest_before_frac": forest_before.astype(np.float32)})
    forest_loss = kept & forest_before
    probable_clearing = forest_loss & (d_bsi > th.bare_bsi_rise_min)
    for r in regs:
        f = r.get("mean_forest_before_frac") or 0
        r["forest_like_before"] = f >= 0.5
        r["indicators"] = [n for n, ok in (("ndvi_drop", True), ("forest_like_before", f >= 0.5),
                                            ("bare_soil_increase", (r.get("mean_delta_bsi") or 0) > th.bare_bsi_rise_min)) if ok]
    gain = comp & (nb < th.ndvi_after_max) & (na > th.ndvi_before_min)

    valid_ha = mask_area_ha(comp, grid)
    loss_ha = mask_area_ha(kept, grid)
    veg_before = comp & (nb > th.ndvi_before_min)
    result = {
        "analysis_type": "vegetation_change",
        "method": "ndvi_threshold_and_spatial_filter",
        "aoi_area_ha": round(grid.width * grid.height * grid.pixel_area_m2 / 10_000, 2),
        "valid_area_ha": round(valid_ha, 2),
        "valid_fraction_of_aoi": round(float(comp.mean()), 4),
        "vegetated_before_ha": round(mask_area_ha(veg_before, grid), 2),
        "candidate_loss_ha": round(loss_ha, 2),
        "candidate_loss_raw_ha": round(mask_area_ha(raw, grid), 2),
        "loss_fraction_of_valid_area": round(safe_fraction(loss_ha, valid_ha) or 0.0, 5),
        "loss_fraction_of_vegetated_before": round(safe_fraction(loss_ha, mask_area_ha(veg_before, grid)) or 0.0, 5),
        "candidate_gain_ha": round(mask_area_ha(gain, grid), 2),
        "forest_like_before_ha": round(mask_area_ha(forest_before, grid), 2),
        "forest_like_loss_ha": round(mask_area_ha(forest_loss, grid), 2),
        "probable_clearing_ha": round(mask_area_ha(probable_clearing, grid), 2),
        "forest_like_loss_fraction": round(safe_fraction(mask_area_ha(forest_loss, grid), mask_area_ha(forest_before, grid)) or 0.0, 5),
        "mean_ndvi_before": round(float(np.nanmean(np.where(comp, nb, np.nan))), 4) if comp.any() else None,
        "mean_ndvi_after": round(float(np.nanmean(np.where(comp, na, np.nan))), 4) if comp.any() else None,
        "regions_detected": len(regs),
        "regions": regs,
        "thresholds": th.model_dump(),
        "excluded": ["SCL invalid on either date", "water (SCL 6) on either date", "regions < min_region_ha"],
    }
    return {"result": result, "masks": {"change": kept, "valid": comp, "forest_loss": forest_loss}, "layers": {"ndvi_before": nb, "ndvi_after": na, "delta": delta}}
