"""Assemble the observed / inferred / not-established report sections from real state."""
from __future__ import annotations

def build_sections(st) -> dict:
    from app.indicators import get

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


def render_evidence_report(state: dict, assets_root) -> str:
    """Portable report: embed only validated local investigation PNGs, never remote URLs."""
    import base64
    import json
    import re
    from pathlib import Path

    inv = state['investigation_id']
    if not re.fullmatch(r'inv_[0-9a-f]{10}', inv):
        raise ValueError('Invalid investigation id')
    payload = {k: state.get(k) for k in (
        'investigation_id', 'claim', 'status', 'created_at', 'updated_at', 'location',
        'temporal_scope', 'verdict', 'measurements', 'headline', 'methodology', 'sources',
        'limitations', 'from_cache', 'error', 'location_resolution')}
    sources = list(payload.get('sources') or [])
    seen = {s.get('url') for s in sources}
    resolution = state.get('location_resolution') or {}
    for source in resolution.get('sources', []):
        if source.get('url') not in seen:
            sources.append({'type': 'geographic', **source})
            seen.add(source.get('url'))
    location = state.get('location') or {}
    if location.get('boundary_source') and location['boundary_source'] not in seen:
        sources.append({'type': 'geographic', 'title': 'Published project boundary',
                        'url': location['boundary_source'], 'retrieved_at': location.get('boundary_retrieved_at')})
    payload['sources'] = sources
    imagery = state.get('imagery') or {}
    payload['images'] = []
    for role, meta in [('Before', imagery.get('before') or {}),
                       ('After', imagery.get('after') or {}),
                       ('Change overlay', {'url': imagery.get('overlay_url')})]:
        url = meta.get('url') or ''
        match = re.fullmatch(r'/assets/' + re.escape(inv) + r'/([a-z0-9_]{1,40}\.png)', url)
        image = {k: meta[k] for k in ('scene_id', 'acquired_at', 'date') if k in meta}
        image.update(role=role, data=None)
        if match:
            path = (Path(assets_root) / inv / match[1]).resolve()
            if Path(assets_root).resolve() in path.parents and path.is_file():
                image['data'] = 'data:image/png;base64,' + base64.b64encode(path.read_bytes()).decode('ascii')
        payload['images'].append(image)
    # Escape HTML delimiters even inside JSON strings to prevent closing the script element.
    data = json.dumps(payload, ensure_ascii=True).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
    template = Path(__file__).with_name('evidence_report.html').read_text(encoding='utf-8')
    return template.replace('__REPORT_DATA__', data)
