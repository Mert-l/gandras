"""Construction & built-up (BuildCheck): candidate new built or construction surface.

Signal: robust multispectral change where the after surface is non-vegetated, not water, and either
the built-up index (NDBI) rose or vegetation was removed. Each changed region is then split by its median bare-soil
index (BSI) on the after date: low-BSI regions (roofs, concrete, asphalt) are the evidence (red); high-BSI regions
(cleared / graded bare ground, earthworks, mine benches, fallow fields) are reported separately (amber, not counted),
because cleared ground alone is not a building. Evidence regions are labelled descriptively:
- vegetation_to_construction: vegetated before, built-like surface after
- bare_to_built: bare before, after shows lower bare-soil index with higher texture (roofs/paving on prepared ground)
- other_surface_change
At 10 m, individual small buildings (< ~0.1-0.5 ha) are not resolved; completion and legality are never established.
"""
from __future__ import annotations

import cv2
import numpy as np
from pydantic import BaseModel

from app.analysis import visualize as viz
from app.analysis.change_detection import clean_mask, regions
from app.analysis.measurements import mask_area_ha
from app.analysis.mining import _gains, bsi
from app.analysis.vegetation import ndvi
from app.indicators.base import Indicator, grid_area_ha, output
from app.indicators.water import mndwi


class BuiltThresholds(BaseModel):
    mad_k: float = 4.0
    magnitude_floor: float = 0.05
    ndvi_after_max: float = 0.30
    ndbi_rise_min: float = 0.03
    ndvi_drop_min: float = 0.20
    min_region_ha: float = 0.5
    open_px: int = 1
    water_nir_max: float = 0.15  # open water is dark in NIR; bright roofs with MNDWI > 0 are not water
    # Region-median after-date BSI below this = built-like surface (evidence); above = bare ground / earthworks.
    # Calibrated on real regions (median raw after BSI): Giga Berlin roofs/paving -0.01..0.08, Giga Texas main
    # roof 0.09; bare/graded ground: Giga Berlin cleared sand 0.20, Giga Texas dirt pads 0.11-0.19, Bingham
    # Canyon mine earthworks 0.12-0.21 (2019-07-12 vs 2024-07-10, previously a 9.9 ha false "construction").
    built_bsi_max: float = 0.10


def ndbi(b: dict) -> np.ndarray:
    s, n = b["B11"], b["B08"]
    with np.errstate(divide="ignore", invalid="ignore"):
        out = (s - n) / (s + n)
    out[~np.isfinite(out)] = np.nan
    return out.astype(np.float32)


def _texture(x: np.ndarray, k: int = 5) -> np.ndarray:
    x = np.nan_to_num(x, nan=float(np.nanmedian(x))).astype(np.float32)
    m = cv2.blur(x, (k, k))
    return np.sqrt(np.maximum(cv2.blur(x * x, (k, k)) - m * m, 0))


def _analyze(before, after, valid, grid, direction="increase", th: BuiltThresholds | None = None):
    th = th or BuiltThresholds()
    comp = valid.copy()
    for b in ("B02", "B03", "B04", "B08", "B11"):
        comp &= np.isfinite(before[b]) & np.isfinite(after[b])
    g = _gains(before, after, comp)
    an = {b: after[b] * g.get(b, 1.0) for b in ("B02", "B03", "B04", "B08", "B11")}
    an["SCL"] = after["SCL"]
    mag = np.sqrt(sum((an[b] - before[b]) ** 2 for b in ("B02", "B04", "B08", "B11"))).astype(np.float32)
    v = mag[comp]
    med = float(np.median(v))
    mad = float(np.median(np.abs(v - med))) * 1.4826
    thr = max(th.magnitude_floor, med + th.mad_k * mad)
    # Indices for the after date use the RAW after bands; the per-band gains are only for the change magnitude.
    # Evidence (real imagery): Giga Berlin, Grünheide (52.395, 13.790), 2019-07-26 vs 2023-07-15: gains were
    # B04 0.64 / B08 0.96 (fitted on dark forest), which pushed the gain-adjusted NDVI of the freshly graded sand
    # above 0.30, so 98% of that cleared area failed "non-vegetated after" and only 15 ha of ~300 ha were flagged.
    nb, na = ndvi(before["B08"], before["B04"]), ndvi(after["B08"], after["B04"])
    d_ndbi = ndbi(after) - ndbi(before)
    bsi_after = bsi(after)
    d_bsi = bsi_after - bsi(before)
    tex_gain = _texture(an["B04"]) - _texture(before["B04"])
    # Water also needs low NIR: bright white roofs can have MNDWI > 0. Evidence: Giga Berlin main hall (55% of a box
    # over the roof was excluded as "water"; roof NIR ~0.28) and Giga Texas (30.2209, -97.6186; 2020-05-19 vs
    # 2023-06-20) main roof (19% excluded; NIR ~0.5) — with this and the raw-index fix the Texas evidence went
    # from 76 ha (roof largely unflagged) to 112 ha, and Berlin from 15 ha to 160 ha (before the BSI split below).
    water_after = (mndwi(after) > 0.0) & (after["B08"] < th.water_nir_max)
    cand = comp & (mag > thr) & (na < th.ndvi_after_max) & ~water_after & (
        (d_ndbi > th.ndbi_rise_min) | ((nb - na) > th.ndvi_drop_min))
    n, lab = cv2.connectedComponents(clean_mask(cand, th.open_px).astype(np.uint8), connectivity=8)
    built = np.zeros_like(cand, dtype=bool)
    cleared = np.zeros_like(cand, dtype=bool)
    for i in range(1, n):
        sel = lab == i
        v = bsi_after[sel]
        v = v[np.isfinite(v)]
        if v.size and float(np.median(v)) < th.built_bsi_max:
            built |= sel
        else:
            cleared |= sel
    layers = {"magnitude": mag, "ndvi_before": nb, "ndvi_after": na, "delta_ndbi": d_ndbi, "delta_bsi": d_bsi,
              "bsi_after": bsi_after, "texture_gain": tex_gain}
    ev, regs = regions(built, grid, th.min_region_ha, layers=layers)
    other, c_regs = regions(cleared, grid, th.min_region_ha)
    for r in regs:
        if (r.get("mean_ndvi_before") or 0) >= 0.35:
            r["pattern"] = "vegetation_to_construction"
        elif (r.get("mean_delta_bsi") or 0) < 0 and (r.get("mean_texture_gain") or 0) > 0:
            r["pattern"] = "bare_to_built"
        else:
            r["pattern"] = "other_surface_change"
    by = {}
    for r in regs:
        by[r["pattern"]] = round(by.get(r["pattern"], 0) + r["area_ha"], 2)
    res = {"analysis_type": "built_up_change", "method": "robust_change_magnitude_with_ndbi_ndvi_rules_and_spatial_filter",
           "aoi_area_ha": grid_area_ha(grid), "valid_area_ha": round(mask_area_ha(comp, grid), 2),
           "candidate_new_built_ha": round(mask_area_ha(ev, grid), 2), "evidence_ha": round(mask_area_ha(ev, grid), 2),
           "cleared_or_earthworks_ha": round(mask_area_ha(other, grid), 2), "cleared_or_earthworks_regions": len(c_regs),
           "area_by_pattern_ha": by, "regions_detected": len(regs), "regions": regs,
           "thresholds": {**th.model_dump(), "magnitude_threshold_used": round(thr, 4)},
           "radiometric_gains_after_to_before": {k: round(x, 4) for k, x in g.items()},
           "excluded": ["SCL invalid on either date", "water after (MNDWI > 0 and NIR < water_nir_max)", "vegetated after (NDVI ≥ 0.30)",
                        "bare-soil-like regions (median after BSI ≥ built_bsi_max): reported as cleared_or_earthworks_ha, "
                        "not counted", "regions < min_region_ha"],
           "caveat": "Candidate new built or construction surface; not proof of a finished or permitted building."}
    prev = {"Built-up index change (NDBI)": viz.delta_rgb(np.where(comp, d_ndbi, np.nan), span=0.3),
            "Spectral change magnitude": viz.magnitude_rgb(np.where(comp, mag, np.nan), thr)}
    legend = {"red": "candidate new built-like surface: roofs / paving (evidence)"}
    if other.any():
        legend["amber"] = ("cleared / bare ground or earthworks (possible construction start, mining or agriculture) "
                           "— not counted")
    return output(res, ev, comp, other=other, previews=prev, legend=legend)


def _observed(r, d):
    out = [f"Candidate new built-like surface (roofs, paving, concrete) covered {r['candidate_new_built_ha']:.1f} ha in "
           f"{r['regions_detected']} regions ≥ {r['thresholds']['min_region_ha']} ha (change above a locally derived "
           "threshold, non-vegetated and not water afterwards, with a built-up-index rise or vegetation removal, and "
           "a low bare-soil index afterwards)."]
    if r["area_by_pattern_ha"]:
        out.append("By pattern: " + ", ".join(f"{k.replace('_', ' ')} {v:.1f} ha" for k, v in
                                              sorted(r["area_by_pattern_ha"].items(), key=lambda x: -x[1])) + ".")
    if r.get("cleared_or_earthworks_ha"):
        out.append(f"A further {r['cleared_or_earthworks_ha']:.1f} ha changed to cleared / bare ground or earthworks "
                   "(bare-soil-like surface); this is shown separately and not counted as built surface.")
    return out


def _inferred(r, d):
    out = []
    by = r["area_by_pattern_ha"]
    if by.get("vegetation_to_construction"):
        out.append("Vegetated land that became a non-vegetated, low-bare-soil, built-up-like surface is consistent with "
                   "new roofs or paving.")
    if by.get("bare_to_built"):
        out.append("Bare ground that became smoother-textured, lower bare-soil-index surface is consistent with roofs or paving "
                   "on a prepared site, i.e. construction progressing.")
    if r.get("cleared_or_earthworks_ha"):
        out.append("Cleared or graded bare ground alone is not evidence of a building: it can be a construction start, "
                   "but also mining, quarrying, earthworks or agriculture.")
    out.append("Spectral change cannot by itself distinguish buildings from paving, stockpiles, greenhouses or solar panels.")
    return out


BUILT_UP = Indicator(
    id="built_up", name="Construction & built-up", short="BuildCheck",
    description="Detects candidate new built or construction surface (site clearing, new roofs/paving). "
                "Cannot resolve single small houses or establish completion or legality.",
    directions=["increase"], default_direction="increase", evidence_label="candidate new built / construction surface",
    keywords=("build", "construct", "factory", "warehouse", "housing", "house", "develop", "settlement", "infrastructure",
              "complex", "structure", "airport", "runway", "road", "highway", "plant", "facility", "resort", "hotel",
              "apartment", "mall", "stadium", "solar farm", "data center", "datacenter", "urban", "illegal"),
    example_claims=["No construction took place on this site after 2019.", "The factory on this site was completed by 2022.",
                    "A building was illegally built in this protected area after 2020."],
    analyze=_analyze, observed=_observed, inferred=_inferred,
    not_established=["Whether any building is finished, occupied or operational.",
                     "Whether construction was legal, permitted or illegal.",
                     "Who built it, and the exact construction dates between observations.",
                     "Individual structures smaller than roughly 0.5 ha (10 m pixels)."],
    vlm_guidance=("Construction: look for new rectangular roofs, paved lots, cleared and graded sites, new roads, "
                  "versus crops, bare fields, stockpiles, greenhouses, solar panels, shadows or seasonal change. "
                  "Do not judge whether a building is finished or legal."),
    significant_ha=2.0, negligible_ha=0.5, requires_persistence=True, confidence_cap="medium",
)
