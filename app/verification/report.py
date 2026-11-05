"""Assemble the observed / inferred / not-established report sections from real state."""
from __future__ import annotations


def build_sections(st) -> dict:
    a, mode = st.analysis or {}, st.mode
    sel = st.selected_scenes
    observed, inferred, not_est, lim = [], [], [], []
    if a:
        b, af = sel["before"]["scene"], sel["after"]["scene"]
        observed.append(f"Sentinel-2 L2A scenes {b['scene_id']} ({b['acquired_at'][:10]}) and "
                        f"{af['scene_id']} ({af['acquired_at'][:10]}) were compared on a common "
                        f"{st.methodology['grid']['resolution_m']:.0f} m grid; {a['common_valid_pct']:.0f}% of the AOI was clear on both.")
        if mode == "forest":
            observed.append(f"NDVI decreased past the loss thresholds over {a['candidate_loss_ha']:.1f} ha "
                            f"({a['regions_detected']} regions ≥ {a['thresholds']['min_region_ha']} ha).")
            observed.append(f"{a['forest_like_loss_ha']:.1f} ha of that loss was on pixels with forest-like canopy before "
                            f"(NDVI ≥ {a['thresholds']['forest_ndvi_min']}, SWIR < {a['thresholds']['forest_swir_max']}); "
                            f"{a['probable_clearing_ha']:.1f} ha also showed a bare-soil-index increase.")
            if a["probable_clearing_ha"] > 0:
                inferred.append("Areas with all three indicators (canopy before, NDVI drop, bare soil after) are consistent with clearing.")
            if a["candidate_loss_ha"] - a["forest_like_loss_ha"] > 1:
                inferred.append(f"The remaining {a['candidate_loss_ha'] - a['forest_like_loss_ha']:.1f} ha of vegetation-index loss "
                                "may reflect pasture/crop drying, harvest or other land management rather than forest removal.")
            not_est += ["Who cleared the land or why.", "Whether any clearing was legal or permitted.",
                        "Exact clearing dates between the two observations."]
        else:
            observed.append(f"Multispectral change above a locally derived threshold covered {a['candidate_changed_area_ha']:.1f} ha "
                            f"in {a['regions_detected']} connected regions ≥ {a['thresholds']['min_region_ha']} ha.")
            pats = {}
            for r in a.get("regions", []):
                pats[r["spectral_pattern"]] = pats.get(r["spectral_pattern"], 0) + r["area_ha"]
            if pats:
                observed.append("Spectral patterns by area: " + ", ".join(f"{k.replace('_', ' ')} {v:.1f} ha" for k, v in sorted(pats.items(), key=lambda x: -x[1])) + ".")
            inferred.append("Changes within already-bare ground near active workings are consistent with earthworks such as "
                            "excavation, dumping or road building, but spectral change alone cannot tell these apart.")
            not_est += ["That the changes are mining expansion rather than other earthworks or surface variation.",
                        "Which company or operator caused the changes.", "Whether any activity was permitted or violated a permit."]
    if st.corroboration and st.corroboration.get("found"):
        observed.append(f"In an independent observation ({st.corroboration['acquired_at'][:10]}), "
                        f"{st.corroboration['persisting_fraction']:.0%} of the detected change area was detected again.")
    va = st.visual_analysis
    if va and va.get("status") == "ok" and va.get("analysis"):
        inferred.append("Visual interpretation (VLM): " + va["analysis"]["summary"])
        lim += [f"VLM: {x}" for x in va["analysis"].get("limitations", [])[:3]]
    elif va:
        lim.append(f"Visual interpretation unavailable ({va.get('error') or 'model error'}); verdict uses measurements only.")
    if a:
        lim.append(f"Sentinel-2 resolution is {st.methodology['grid']['resolution_m']:.0f} m; changes smaller than "
                   f"{a['thresholds']['min_region_ha']} ha are not reported.")
        lim.append("Thresholds are configurable starting heuristics, not validated universal criteria.")
        if not a["pair"]["seasonally_comparable"]:
            lim.append(f"Observation dates differ by {a['pair']['seasonal_gap_days']} days in season.")
    ci = st.claim_interpretation or {}
    if ci.get("referenced_date") and st.selected_scenes.get("before"):
        gap = ci.get("reference_gap_note")
        if gap:
            lim.append(gap)
    return {"observed": observed, "inferred": inferred, "not_established": not_est, "limitations": lim}
