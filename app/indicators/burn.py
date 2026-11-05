"""Fire & burn scars (BurnCheck): differenced Normalized Burn Ratio (dNBR) with B12."""
from __future__ import annotations

import numpy as np
from pydantic import BaseModel

from app.analysis import visualize as viz
from app.analysis.change_detection import clean_mask, regions
from app.analysis.measurements import mask_area_ha
from app.analysis.vegetation import ndvi
from app.indicators.base import STANDARD_BANDS, Indicator, grid_area_ha, output


class BurnThresholds(BaseModel):
    dnbr_min: float = 0.27       # USGS/Key & Benson: >= 0.27 is moderate-low severity and above
    ndvi_drop_min: float = 0.10
    min_region_ha: float = 1.0
    open_px: int = 1


def nbr(b: dict) -> np.ndarray:
    n, s = b["B08"], b["B12"]
    with np.errstate(divide="ignore", invalid="ignore"):
        out = (n - s) / (n + s)
    out[~np.isfinite(out)] = np.nan
    return out.astype(np.float32)


def _analyze(before, after, valid, grid, direction="increase", th: BurnThresholds | None = None):
    th = th or BurnThresholds()
    d = nbr(before) - nbr(after)
    dn = ndvi(after["B08"], after["B04"]) - ndvi(before["B08"], before["B04"])
    comp = valid & np.isfinite(d) & np.isfinite(dn) & (after["SCL"] != 6) & (before["SCL"] != 6)
    ev, regs = regions(clean_mask(comp & (d > th.dnbr_min) & (dn < -th.ndvi_drop_min), th.open_px), grid,
                       th.min_region_ha, layers={"dnbr": d, "delta_ndvi": dn})
    sev = {"moderate_low (0.27-0.44)": comp & ev & (d < 0.44), "moderate_high (0.44-0.66)": comp & ev & (d >= 0.44) & (d < 0.66),
           "high (>=0.66)": comp & ev & (d >= 0.66)}
    r = {"analysis_type": "burn_scar", "method": "dnbr_threshold_with_ndvi_drop_and_spatial_filter",
         "aoi_area_ha": grid_area_ha(grid), "valid_area_ha": round(mask_area_ha(comp, grid), 2),
         "burned_area_ha": round(mask_area_ha(ev, grid), 2), "evidence_ha": round(mask_area_ha(ev, grid), 2),
         "area_by_severity_ha": {k: round(mask_area_ha(m, grid), 2) for k, m in sev.items()},
         "regions_detected": len(regs), "regions": regs, "thresholds": th.model_dump(),
         "excluded": ["SCL invalid on either date", "water", "regions < min_region_ha"]}
    return output(r, ev, comp, previews={"dNBR (burn severity)": viz.delta_rgb(np.where(comp, -d, np.nan), span=0.8)},
                  legend={"red": "probable burn scar (evidence)"})


def _observed(r, d):
    return [f"dNBR > {r['thresholds']['dnbr_min']} with an NDVI drop covered {r['burned_area_ha']:.1f} ha in "
            f"{r['regions_detected']} regions ≥ {r['thresholds']['min_region_ha']} ha.",
            "By severity class: " + ", ".join(f"{k} {v:.1f} ha" for k, v in r["area_by_severity_ha"].items()) + "."]


def _inferred(r, d):
    return ["A large NBR drop with vegetation loss is the standard signature of fire; harvest, clearing or drought can "
            "produce smaller similar signals. Scars fade with regrowth, so long gaps can hide older fires."]


BURN = Indicator(
    id="burn", name="Fire & burn scars", short="BurnCheck",
    description="Detects probable burn scars with the differenced Normalized Burn Ratio (NIR vs SWIR-2).",
    directions=["increase"], default_direction="increase", evidence_label="probable burn scar",
    keywords=("fire", "burn", "wildfire", "blaze", "scorch", "charred", "arson", "ash"),
    example_claims=["No fires burned in this area during 2023.", "A wildfire burned this hillside in summer 2021."],
    analyze=_analyze, observed=_observed, inferred=_inferred,
    not_established=["What started any fire or who is responsible.", "The exact fire dates between the observations."],
    vlm_guidance=("Fire: look for dark/brown burn scars with sharp fire-front edges replacing vegetation, versus "
                  "harvested fields, shadows, water or wet soil."),
    significant_ha=5.0, negligible_ha=1.0, confidence_cap="medium", bands=STANDARD_BANDS[:-1] + ["B12", "SCL"],
    min_region_ha=1.0,
)
