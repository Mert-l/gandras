"""Assemble the observed / inferred / not-established report sections from real state."""
from __future__ import annotations

from app.indicators import get


def build_sections(st) -> dict:
    a = st.analysis or {}
    observed, inferred, not_est, lim = [], [], [], []
    ind = get(st.indicator) if st.indicator else None
    direction = (st.claim_interpretation or {}).get("direction", "any")
    if a and ind:
        sel = st.selected_scenes
        b, af = sel["before"]["scene"], sel["after"]["scene"]
        observed.append(f"Sentinel-2 L2A scenes {b['scene_id']} ({b['acquired_at'][:10]}) and {af['scene_id']} "
                        f"({af['acquired_at'][:10]}) were compared on a common {st.methodology['grid']['resolution_m']:.0f} m grid; "
                        f"{a['common_valid_pct']:.0f}% of the AOI was clear on both.")
        observed += ind.observed(a, direction)
        inferred += ind.inferred(a, direction)
        not_est += list(ind.not_established)
        lim.append(f"Sentinel-2 resolution is {st.methodology['grid']['resolution_m']:.0f} m; regions smaller than "
                   f"{a['thresholds'].get('min_region_ha', ind.min_region_ha)} ha are not reported.")
        lim.append("Thresholds are configurable starting heuristics, not validated universal criteria.")
        if not a["pair"]["seasonally_comparable"]:
            lim.append(f"Observation dates differ by {a['pair']['seasonal_gap_days']} days in season.")
        if ind.is_fallback:
            lim.append("No specific indicator matched the claim; only general surface change was measured.")
    if st.corroboration and st.corroboration.get("found"):
        observed.append(f"In an independent observation ({st.corroboration['acquired_at'][:10]}), "
                        f"{st.corroboration['persisting_fraction']:.0%} of the evidence area was detected again.")
    va = st.visual_analysis
    if va and va.get("status") == "ok" and va.get("analysis"):
        inferred.append("Visual interpretation (VLM): " + va["analysis"]["summary"])
        lim += [f"VLM: {x}" for x in va["analysis"].get("limitations", [])[:3]]
    elif va:
        lim.append(f"Visual interpretation unavailable ({va.get('error') or 'model error'}); verdict uses measurements only.")
    gap = (st.claim_interpretation or {}).get("reference_gap_note")
    if gap:
        lim.append(gap)
    return {"observed": observed, "inferred": inferred, "not_established": not_est, "limitations": lim}
