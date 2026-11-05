"""Surface disturbance (MineWatch): robust multispectral change with BSI/NDVI context."""
from __future__ import annotations

from app.analysis import visualize as viz
from app.analysis.mining import analyze_surface_disturbance
from app.indicators.base import Indicator, output


def _analyze(before, after, valid, grid, direction="any"):
    res = analyze_surface_disturbance(before, after, valid, grid)
    r = res["result"]
    r["evidence_ha"] = r["candidate_changed_area_ha"]
    thr = r["thresholds"]["magnitude_threshold_used"]
    prev = {"Bare-soil index change": viz.delta_rgb(res["layers"]["delta_bsi"], span=0.3),
            "Spectral change magnitude": viz.magnitude_rgb(res["layers"]["magnitude"], thr)}
    return output(r, res["masks"]["change"], res["masks"]["valid"], previews=prev,
                  legend={"red": "candidate surface disturbance (evidence)"})


def _observed(r, d):
    out = [f"Multispectral change above a locally derived threshold covered {r['candidate_changed_area_ha']:.1f} ha "
           f"in {r['regions_detected']} connected regions ≥ {r['thresholds']['min_region_ha']} ha."]
    pats: dict[str, float] = {}
    for x in r.get("regions", []):
        pats[x["spectral_pattern"]] = pats.get(x["spectral_pattern"], 0) + x["area_ha"]
    if pats:
        out.append("Spectral patterns by area: " + ", ".join(f"{k.replace('_', ' ')} {v:.1f} ha"
                                                        for k, v in sorted(pats.items(), key=lambda x: -x[1])) + ".")
    return out


def _inferred(r, d):
    return ["Changes within already-bare ground near active workings are consistent with earthworks such as excavation, "
            "dumping or road building, but spectral change alone cannot tell these apart."]


def build(id_, name, short, description, keywords, examples, label, not_est, guidance, sig, neg, fallback=False):
    return Indicator(id=id_, name=name, short=short, description=description, directions=["any"], default_direction="any",
                     evidence_label=label, keywords=keywords, example_claims=examples, analyze=_analyze,
                     observed=_observed, inferred=_inferred, not_established=not_est, vlm_guidance=guidance,
                     significant_ha=sig, negligible_ha=neg, requires_persistence=True, confidence_cap="medium",
                     is_fallback=fallback, min_region_ha=1.0)


SURFACE = build(
    "surface_disturbance", "Mining & earthworks", "MineWatch",
    "Detects candidate surface disturbance (excavation, dumps, earthworks) as robust multispectral change. Measures change, not cause.",
    ("mine", "mining", "pit", "quarry", "excavat", "tailings", "waste dump", "earthwork", "dig", "spoil", "open-cast", "opencast"),
    ["The mining operation at this site stopped expanding after 2019.", "The quarry has grown since 2021."],
    "candidate surface disturbance",
    ["That the changes are mining expansion rather than other earthworks or surface variation.",
     "Which company or operator caused the changes.", "Whether any activity was permitted or violated a permit."],
    ("Mining: look for expanded pits/benches, new waste dumps or tailings, new roads, cleared ground, versus changes "
     "plausibly unrelated to mining (shadows, moisture, illumination)."),
    10.0, 2.0)
