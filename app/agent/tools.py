"""Agent tools. Each executes real pipeline code and returns (summary, details).

Tools raise ToolError for expected investigation problems (e.g. no imagery) and let unexpected
exceptions propagate so they are reported as execution failures, not scientific conclusions.
"""
from __future__ import annotations

import time
from datetime import date, timedelta

import numpy as np

from app.agent.state import InvestigationState, asset_dir
from app.analysis import visualize as viz
from app.config import get_settings
from app.satellite import cache
from app.satellite.aoi import AOI, AnalysisGrid
from app.satellite.preprocessing import common_valid, render_rgb, shared_stretch, valid_mask
from app.satellite.raster_loader import load_scene
from app.satellite.scene_selector import describe_pair, rank_scenes
from app.satellite.stac_client import SceneMeta, search_scenes


class ToolError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def aoi_of(st: InvestigationState) -> AOI:
    loc = st.location
    return AOI(name=loc["name"], lat=loc["latitude"], lon=loc["longitude"], width_m=loc["width_km"] * 1000,
               geometry=loc.get("geometry") if loc.get("boundary_status") == "verified" else None)


def grid_of(st: InvestigationState) -> AnalysisGrid:
    return aoi_of(st).grid(get_settings().analysis_resolution_m)


def _scene(d: dict) -> SceneMeta:
    return SceneMeta.model_validate(d["scene"])


# ---------------------------------------------------------------- imagery search + quality

def find_observation(st: InvestigationState, role: str, center: date, window_days: int) -> tuple[str, dict]:
    """Search the catalogue around `center` and inspect local SCL quality of the nearest candidates."""
    s = get_settings()
    aoi, grid = aoi_of(st), grid_of(st)
    start, end = center - timedelta(days=window_days), center + timedelta(days=window_days)
    if start > date.today():
        raise ToolError("NO_SATELLITE_DATA", f"Requested window {start}..{end} is in the future")
    end = min(end, date.today())
    t0 = time.time()
    scenes = search_scenes(aoi.bbox_wgs84(), start.isoformat(), end.isoformat(), max_cloud=max(s.max_scene_cloud_percent, 60))
    ranked = rank_scenes(scenes, grid, center, s.max_local_invalid_percent, max_inspect=6)
    st.search_attempts[role] = st.search_attempts.get(role, 0) + 1
    inspected = [{"scene_id": r.scene.scene_id, "date": str(r.scene.acquired_at.date()),
                  "scene_cloud_pct": r.scene.cloud_cover, "local_invalid_pct": r.quality["invalid_pct"],
                  "local_cloud_pct": r.quality["cloud_pct"], "usable": r.usable} for r in ranked]
    st.candidates[role] = inspected
    details = {"role": role, "window": [start.isoformat(), end.isoformat()], "catalogue_hits": len(scenes),
               "inspected": inspected, "max_local_invalid_pct": s.max_local_invalid_percent,
               "duration_s": round(time.time() - t0, 1)}
    best = next((r for r in ranked if r.usable), None)
    if best is None:
        worst = ", ".join(f"{i['date']} {i['local_invalid_pct']:.0f}% obscured" for i in inspected[:3]) or "no scenes"
        details["selected"] = None
        return (f"No usable {role} observation within {start}..{end}: {len(scenes)} catalogue hits; nearest "
                f"inspected: {worst}."), details
    st.selected_scenes[role] = {"scene": best.scene.model_dump(mode="json"), "quality": best.quality,
                                "days_from_target": best.days_from_target}
    details["selected"] = best.scene.scene_id
    rejected = [i for i in inspected if not i["usable"]]
    rej = ""
    if rejected:
        r0 = rejected[0]
        rej = f" Rejected {len(rejected)} cloudier scene(s), e.g. {r0['date']} ({r0['local_invalid_pct']:.0f}% of AOI obscured despite {r0['scene_cloud_pct'] or 0:.0f}% scene-wide cloud)."
    return (f"Selected {role} scene {best.scene.scene_id} acquired {best.scene.acquired_at.date()} "
            f"({best.days_from_target:+d} days from target), {best.quality['invalid_pct']:.1f}% of AOI invalid.{rej}"), details


# ---------------------------------------------------------------- change analysis

def _load_pair(st: InvestigationState, before_role="before", after_role="after"):
    from app.indicators import get
    grid = grid_of(st)
    bands = get(st.indicator).bands
    b, a = _scene(st.selected_scenes[before_role]), _scene(st.selected_scenes[after_role])
    B, A = load_scene(b, grid, bands=bands), load_scene(a, grid, bands=bands)
    vb, va = valid_mask(B["SCL"]), valid_mask(A["SCL"])
    inside = aoi_of(st).mask(grid)
    vb, va = vb & inside, va & inside
    return grid, b, a, B, A, vb, va, common_valid(vb, va)


def _direction(st: InvestigationState) -> str:
    return (st.claim_interpretation or {}).get("direction", "any")


def run_analysis(st: InvestigationState) -> tuple[str, dict]:
    from app.indicators import get
    ind = get(st.indicator)
    grid, b, a, B, A, vb, va, both = _load_pair(st)
    inside = aoi_of(st).mask(grid)
    valid_fraction = float(both.sum()) / max(1, int(inside.sum()))
    if valid_fraction < 0.3:
        raise ToolError("INSUFFICIENT_CLEAR_IMAGERY", f"Only {valid_fraction:.0%} of the AOI is clear on both dates")
    res = ind.analyze(B, A, both, grid, _direction(st))
    r = res["result"]
    pair = describe_pair(b, a)
    r["pair"] = pair
    r["common_valid_pct"] = round(100 * valid_fraction, 2)
    r["aoi_area_ha"] = round(int(inside.sum()) * grid.pixel_area_m2 / 10000, 2)
    r["valid_fraction_of_aoi"] = round(valid_fraction, 4)
    r["indicator"] = ind.id
    r["evidence_label"] = ind.evidence_label

    out = asset_dir(st.investigation_id)
    stretch = shared_stretch([B, A], [both, both])
    rgb_b, rgb_a = render_rgb(B, stretch, vb, 1.4), render_rgb(A, stretch, va, 1.4)
    viz._save(rgb_b, out / "before.png")
    viz._save(rgb_a, out / "after.png")
    evidence, other = res["masks"]["evidence"], res["masks"].get("other")
    viz.overlay(rgb_a, evidence, r["regions"], out / "overlay.png", other=other)
    viz.mask_rgba(evidence, out / "mask.png", other=other)
    extra = {}
    for i, (label, img) in enumerate(res["previews"].items()):
        name = f"extra_{i}.png"
        viz._save(img, out / name)
        extra[label] = name
    np.save(out / "change_mask.npy", evidence)  # the mask the verdict rests on; used for persistence
    r["stretch"] = stretch
    st.analysis = r
    st.methodology = {"indicator": f"{ind.name} ({ind.id})", "analysis": r["method"], "thresholds": r["thresholds"],
                      "grid": grid.model_dump(), "pixel_area_m2": grid.pixel_area_m2, "bands": ind.bands,
                      "invalid_scl_classes": "no data, saturated, cloud shadow, cloud (medium/high), thin cirrus, snow/ice; "
                                             "plus 50 m buffer around cloud/shadow",
                      "calibration": "reflectance = DN*scale + offset from each asset's STAC raster:bands",
                      "resampling": "WarpedVRT onto common UTM grid; bilinear reflectance, nearest SCL",
                      "evidence_rules": {"significant_ha": ind.significant_ha, "negligible_ha": ind.negligible_ha,
                                         "requires_persistence": ind.requires_persistence,
                                         "confidence_cap": ind.confidence_cap, "is_fallback": ind.is_fallback}}
    st.imagery = {
        "before": {"date": str(b.acquired_at.date()), "acquired_at": b.acquired_at.isoformat(), "scene_id": b.scene_id,
                   "url": f"/assets/{st.investigation_id}/before.png"},
        "after": {"date": str(a.acquired_at.date()), "acquired_at": a.acquired_at.isoformat(), "scene_id": a.scene_id,
                  "url": f"/assets/{st.investigation_id}/after.png"},
        "overlay_url": f"/assets/{st.investigation_id}/overlay.png",
        "mask_url": f"/assets/{st.investigation_id}/mask.png",
        "extra": {k: f"/assets/{st.investigation_id}/{v}" for k, v in extra.items()},
    }
    summ = (f"{ind.name}: valid comparison area {r['valid_area_ha']:.0f} ha ({r['common_valid_pct']:.0f}% of AOI). "
            f"{ind.evidence_label.capitalize()}: {r['evidence_ha']:.1f} ha in {len(r['regions'])} region(s). "
            + " ".join(ind.observed(r, _direction(st))[:1]))
    if not pair["seasonally_comparable"]:
        summ += f" Warning: dates are {pair['seasonal_gap_days']} days apart in season."
    details = {k: v for k, v in r.items() if not k.endswith("regions") and k not in ("stretch",)}
    details["top_regions"] = r["regions"][:5]
    return summ, details


def corroborate(st: InvestigationState, window_days: int = 45) -> tuple[str, dict]:
    """Retrieve an independent later observation and test whether the evidence persists."""
    from app.indicators import get
    if not st.analysis or not st.analysis.get("regions"):
        raise ToolError("ANALYSIS_FAILED", "No evidence regions to corroborate")
    ind = get(st.indicator)
    a1 = _scene(st.selected_scenes["after"])
    t = a1.acquired_at.date()
    center = t + timedelta(days=window_days + 15)
    if center > date.today() - timedelta(days=10):
        center = t - timedelta(days=window_days + 15)
    s = get_settings()
    aoi, grid = aoi_of(st), grid_of(st)
    scenes = [x for x in search_scenes(aoi.bbox_wgs84(), (center - timedelta(days=window_days)).isoformat(),
                                       min(center + timedelta(days=window_days), date.today()).isoformat(), max_cloud=60)
              if abs((x.acquired_at.date() - t).days) >= 15]
    ranked = [r for r in rank_scenes(scenes, grid, center, s.max_local_invalid_percent, max_inspect=5) if r.usable]
    if not ranked:
        return ("No independent usable observation found to check persistence of the evidence.",
                {"found": False, "searched_around": center.isoformat()})
    third = ranked[0].scene
    st.selected_scenes["corroboration"] = {"scene": third.model_dump(mode="json"), "quality": ranked[0].quality}
    grid, b, a, B, A, vb, va, both = _load_pair(st, "before", "corroboration")
    res = ind.analyze(B, A, both, grid, _direction(st))
    import cv2
    orig = np.load(asset_dir(st.investigation_id) / "change_mask.npy")
    k = np.ones((5, 5), np.uint8)
    # Persistence asks whether the change is still detected at all (evidence or secondary class):
    # an indicator's classification can wobble between dates (e.g. a roof read as bare surface),
    # which must not look like the change disappearing. Class stability is reported separately.
    other = res["masks"].get("other")
    changed = res["masks"]["evidence"] | (other if other is not None else False)
    new = cv2.dilate(changed.astype(np.uint8), k).astype(bool)
    same_class = cv2.dilate(res["masks"]["evidence"].astype(np.uint8), k).astype(bool)
    comparable = orig & both
    persist = float((comparable & new).sum()) / max(1, int(comparable.sum()))
    stability = float((comparable & same_class).sum()) / max(1, int(comparable.sum()))
    per_region = []
    for r in st.analysis["regions"][:8]:
        x, y, w, h = r["bbox_px"]
        sel = orig[y:y + h, x:x + w] & both[y:y + h, x:x + w]
        frac = float((sel & new[y:y + h, x:x + w]).sum()) / max(1, int(sel.sum()))
        per_region.append({"id": r["id"], "area_ha": r["area_ha"], "persisting_fraction": round(frac, 3)})
    out = {"found": True, "scene_id": third.scene_id, "acquired_at": third.acquired_at.isoformat(),
           "persisting_fraction": round(persist, 3), "same_class_fraction": round(stability, 3),
           "per_region": per_region, "evidence_mask": ind.evidence_label,
           "compared": f"{b.acquired_at.date()} vs {third.acquired_at.date()}"}
    st.corroboration = out
    return (f"Independent observation {third.scene_id} ({third.acquired_at.date()}): {persist:.0%} of the original "
            f"{ind.evidence_label} area is still detected as changed against the before image "
            f"({stability:.0%} in the same evidence class)."), out


def cache_hit_rate() -> dict:
    return dict(cache.STATS)
