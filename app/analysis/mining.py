"""MineWatch: candidate surface-disturbance detection.

Detects *candidate surface disturbances* (spectral change in red/NIR/SWIR, with BSI and NDVI
context). It does not classify disturbances as excavation; that is left to visual interpretation
and is reported with uncertainty.
"""
from __future__ import annotations

import numpy as np
from pydantic import BaseModel

from app.analysis.change_detection import clean_mask, regions
from app.analysis.measurements import mask_area_ha, safe_fraction
from app.analysis.vegetation import ndvi
from app.satellite.aoi import AnalysisGrid

BANDS = ("B04", "B08", "B11")


class SurfaceThresholds(BaseModel):
    mad_k: float = 5.0            # threshold = median + k * 1.4826 * MAD of the magnitude
    magnitude_floor: float = 0.06  # never flag changes smaller than this reflectance magnitude
    min_region_ha: float = 1.0
    open_px: int = 1
    normalize_radiometry: bool = True


def bsi(b: dict[str, np.ndarray]) -> np.ndarray:
    """Bare Soil Index: ((SWIR+RED)-(NIR+BLUE)) / ((SWIR+RED)+(NIR+BLUE))."""
    a, c = b["B11"] + b["B04"], b["B08"] + b["B02"]
    den = a + c
    with np.errstate(divide="ignore", invalid="ignore"):
        out = (a - c) / den
    out[~np.isfinite(out) | (np.abs(den) < 1e-6)] = np.nan
    return out.astype(np.float32)


def _gains(before, after, valid) -> dict[str, float]:
    """Per-band median ratio before/after over common valid pixels (relative radiometric normalization)."""
    g = {}
    for b in ("B02", "B04", "B08", "B11"):
        mb, ma = np.nanmedian(before[b][valid]), np.nanmedian(after[b][valid])
        g[b] = float(mb / ma) if ma > 1e-4 else 1.0
    return g


def analyze_surface_disturbance(before: dict[str, np.ndarray], after: dict[str, np.ndarray],
                                valid: np.ndarray, grid: AnalysisGrid,
                                th: SurfaceThresholds | None = None) -> dict:
    th = th or SurfaceThresholds()
    finite = valid.copy()
    for b in ("B02", "B04", "B08", "B11"):
        finite &= np.isfinite(before[b]) & np.isfinite(after[b])
    if not finite.any():
        raise ValueError("No common valid pixels")

    gains = _gains(before, after, finite) if th.normalize_radiometry else {b: 1.0 for b in ("B02", "B04", "B08", "B11")}
    after_n = {b: after[b] * gains[b] if b in gains else after[b] for b in after}

    mag = np.sqrt(sum((after_n[b] - before[b]) ** 2 for b in BANDS)).astype(np.float32)
    mag[~finite] = np.nan
    v = mag[finite]
    med = float(np.median(v))
    mad = float(np.median(np.abs(v - med))) * 1.4826
    thr = max(th.magnitude_floor, med + th.mad_k * mad)

    d_bsi = (bsi(after_n) - bsi(before)).astype(np.float32)
    nb, na = ndvi(before["B08"], before["B04"]), ndvi(after_n["B08"], after_n["B04"])
    d_ndvi = (na - nb).astype(np.float32)
    d_bright = (sum(after_n[b] for b in BANDS) - sum(before[b] for b in BANDS)) / 3

    raw = finite & (mag > thr)
    kept, regs = regions(clean_mask(raw, th.open_px), grid, th.min_region_ha,
                         layers={"magnitude": mag, "delta_bsi": d_bsi, "delta_ndvi": d_ndvi,
                                 "ndvi_before": nb, "delta_brightness": d_bright})
    for r in regs:
        r["spectral_pattern"] = _pattern(r)

    valid_ha = mask_area_ha(finite, grid)
    changed_ha = mask_area_ha(kept, grid)
    bare_gain = kept & (d_bsi > 0.05) & (d_ndvi < -0.1)
    result = {
        "analysis_type": "surface_disturbance",
        "method": "multispectral_change_magnitude_robust_threshold_and_spatial_filter",
        "aoi_area_ha": round(grid.width * grid.height * grid.pixel_area_m2 / 10_000, 2),
        "valid_area_ha": round(valid_ha, 2),
        "valid_fraction_of_aoi": round(float(finite.mean()), 4),
        "candidate_changed_area_ha": round(changed_ha, 2),
        "candidate_changed_raw_ha": round(mask_area_ha(raw, grid), 2),
        "changed_fraction_of_valid_area": round(safe_fraction(changed_ha, valid_ha) or 0.0, 5),
        "vegetation_to_bare_ha": round(mask_area_ha(bare_gain, grid), 2),
        "brightening_ha": round(mask_area_ha(kept & (d_bright > 0), grid), 2),
        "darkening_ha": round(mask_area_ha(kept & (d_bright <= 0), grid), 2),
        "regions_detected": len(regs),
        "regions": regs,
        "thresholds": {**th.model_dump(), "magnitude_threshold_used": round(thr, 4),
                       "magnitude_median": round(med, 4), "magnitude_mad": round(mad, 4)},
        "radiometric_gains_after_to_before": {k: round(v, 4) for k, v in gains.items()},
        "excluded": ["SCL invalid on either date", "regions < min_region_ha"],
        "caveat": "Candidate surface disturbances, not classified excavation.",
    }
    return {"result": result, "masks": {"change": kept, "valid": finite},
            "layers": {"magnitude": mag, "delta_bsi": d_bsi, "delta_ndvi": d_ndvi}}


def _pattern(r: dict) -> str:
    """Deterministic spectral description of a region (descriptive, not causal)."""
    dn, db = r.get("mean_delta_ndvi") or 0, r.get("mean_delta_bsi") or 0
    nb = r.get("mean_ndvi_before") or 0
    if nb >= 0.35 and dn < -0.15 and db > 0.03:
        return "vegetated_to_bare"
    if nb < 0.25 and abs(dn) < 0.1:
        return "change_within_bare_ground"
    if dn > 0.15:
        return "vegetation_increase"
    return "mixed_or_other"
