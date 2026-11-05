"""Forest clearing (ForestCheck): probable clearing = canopy before + NDVI drop + bare-soil rise."""
from __future__ import annotations

from app.analysis import visualize as viz
from app.analysis.vegetation import analyze_vegetation_change
from app.indicators.base import Indicator, output


def _analyze(before, after, valid, grid, direction="decrease"):
    res = analyze_vegetation_change(before, after, valid, grid)
    r = res["result"]
    r["evidence_ha"] = r["probable_clearing_ha"]
    ev = res["masks"]["evidence"]
    prev = {"NDVI before": viz.ndvi_rgb(res["layers"]["ndvi_before"]), "NDVI after": viz.ndvi_rgb(res["layers"]["ndvi_after"]),
            "NDVI change": viz.delta_rgb(res["layers"]["delta"])}
    return output(r, ev, res["masks"]["valid"], other=res["masks"]["change"] & ~ev, previews=prev,
                  legend={"red": "probable forest clearing (evidence)", "amber": "other vegetation-index loss"})


def _observed(r, d):
    th = r["thresholds"]
    return [f"NDVI decreased past the loss thresholds over {r['candidate_loss_ha']:.1f} ha ({r['regions_detected']} regions ≥ {th['min_region_ha']} ha).",
            f"{r['forest_like_loss_ha']:.1f} ha of that loss was on pixels with forest-like canopy before (NDVI ≥ {th['forest_ndvi_min']}, "
            f"SWIR < {th['forest_swir_max']}); {r['probable_clearing_ha']:.1f} ha in {r['clearing_regions_detected']} regions also showed a "
            "bare-soil-index increase (red on the overlay; other vegetation loss in amber)."]


def _inferred(r, d):
    out = []
    if r["probable_clearing_ha"] > 0:
        out.append("Areas with all three indicators (canopy before, NDVI drop, bare soil after) are consistent with clearing.")
    if r["candidate_loss_ha"] - r["forest_like_loss_ha"] > 1:
        out.append(f"The remaining {r['candidate_loss_ha'] - r['forest_like_loss_ha']:.1f} ha of vegetation-index loss may reflect "
                   "pasture/crop drying, harvest or other land management rather than forest removal.")
    return out


def _notes(r, d):
    if r["candidate_loss_ha"] >= 5 and r["probable_clearing_ha"] < 5:
        return [f"{r['candidate_loss_ha']:.1f} ha of general vegetation-index loss, but only {r['probable_clearing_ha']:.1f} ha was "
                "forest-like canopy with a bare-soil increase; the rest may be pasture, crops or seasonal change."]
    return []


FOREST = Indicator(
    id="forest_clearing", name="Forest clearing", short="ForestCheck",
    description="Detects probable removal of dense forest canopy (NDVI drop from canopy-like pixels plus a bare-soil increase).",
    directions=["decrease"], default_direction="decrease", evidence_label="probable forest clearing",
    keywords=("forest", "tree", "deforest", "canopy", "logging", "jungle", "woodland", "rainforest", "clear-cut", "clearcut"),
    example_claims=["No forest was cleared in this area after 2020.", "This forest has been cut down since 2019."],
    analyze=_analyze, observed=_observed, inferred=_inferred, notes=_notes,
    not_established=["Who cleared the land or why.", "Whether any clearing was legal or permitted.",
                     "Exact clearing dates between the two observations."],
    vlm_guidance=("Forest: look for canopy removal (geometric clearing edges, burn scars, bare soil) versus pasture/crop "
                  "drying, harvest, haze or seasonal change."),
    significant_ha=5.0, negligible_ha=1.0,
)
