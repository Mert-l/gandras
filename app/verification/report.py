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


_MODEL_KEYS = ('id', 'label', 'provider', 'model', 'location', 'vision')
_STEP_KEYS = ('index', 'action', 'status', 'decided_by', 'summary', 'started_at')


def _png_size(blob: bytes):
    """(width, height) from a PNG IHDR chunk, or None when the bytes are not a PNG."""
    if blob[:8] == b'\x89PNG\r\n\x1a\n' and blob[12:16] == b'IHDR':
        return int.from_bytes(blob[16:20], 'big'), int.from_bytes(blob[20:24], 'big')
    return None


def _embed_local_png(url, inv: str, assets_root):
    """Data URI + pixel size for /assets/<inv>/<name>.png inside assets_root; never anything else."""
    import base64
    import re
    from pathlib import Path

    match = re.fullmatch(r'/assets/' + re.escape(inv) + r'/([a-z0-9_]{1,40}\.png)', url if isinstance(url, str) else '')
    if not match:
        return None, None
    root = Path(assets_root).resolve()
    path = (root / inv / match[1]).resolve()
    if root not in path.parents or not path.is_file():
        return None, None
    blob = path.read_bytes()
    return 'data:image/png;base64,' + base64.b64encode(blob).decode('ascii'), _png_size(blob)


def _clean_osint(o):
    """Open-source context: plain values only; links are filtered to http(s) again in the template."""
    if not isinstance(o, dict):
        return None
    keep = {k: o.get(k) for k in ('status', 'trigger', 'summary', 'note', 'queries', 'counts', 'started_at', 'finished_at')}
    keep['findings'] = [{k: str(f.get(k) or '') for k in ('quote', 'stance', 'url', 'title', 'retrieved_at')}
                        for f in (o.get('findings') or []) if isinstance(f, dict)][:12]
    keep['sources'] = [{k: str(s.get(k) or '') for k in ('url', 'title', 'retrieved_at')}
                       for s in (o.get('sources') or []) if isinstance(s, dict)][:12]
    return keep


def _clean_model(model):
    """Model descriptor (may be absent): keep only known scalar fields."""
    if not isinstance(model, dict):
        return None
    out = {k: model[k] for k in _MODEL_KEYS if isinstance(model.get(k), (str, bool, int, float))}
    return out or None


def render_evidence_report(state: dict, assets_root) -> str:
    """Portable report: embed only validated local investigation PNGs, never remote URLs."""
    import json
    import re
    from datetime import datetime, timezone
    from pathlib import Path

    inv = state['investigation_id']
    if not isinstance(inv, str) or not re.fullmatch(r'inv_[0-9a-f]{10}', inv):
        raise ValueError('Invalid investigation id')
    payload = {k: state.get(k) for k in (
        'investigation_id', 'claim', 'status', 'created_at', 'updated_at', 'location',
        'temporal_scope', 'verdict', 'measurements', 'headline', 'methodology', 'sources',
        'limitations', 'from_cache', 'error', 'location_resolution', 'indicator', 'indicator_info',
        'requested_indicator', 'claim_interpretation', 'visual_analysis', 'corroboration',
        'evidence_assessment', 'mode')}
    payload['report_generated_at'] = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    payload['model'] = _clean_model(state.get('model'))
    payload['vision_model'] = _clean_model(state.get('vision_model'))
    payload['osint'] = _clean_osint(state.get('osint'))
    payload['steps'] = [{k: step.get(k) for k in _STEP_KEYS} for step in (state.get('steps') or [])
                        if isinstance(step, dict)]
    sources = [s for s in (payload.get('sources') or []) if isinstance(s, dict)]
    seen = {s.get('url') for s in sources}
    resolution = state.get('location_resolution') or {}
    for source in resolution.get('sources') or []:
        if isinstance(source, dict) and source.get('url') not in seen:
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
        image = {k: meta[k] for k in ('scene_id', 'acquired_at', 'date') if k in meta}
        image['data'], size = _embed_local_png(meta.get('url'), inv, assets_root)
        image.update(role=role, width=size and size[0], height=size and size[1])
        payload['images'].append(image)
    # Derived index maps (NDVI, NDBI, dNBR, ...): only validated local PNGs; no URLs leave this function.
    payload['extra_images'] = []
    extra = imagery.get('extra') if isinstance(imagery.get('extra'), dict) else {}
    for label, url in extra.items():
        data, size = _embed_local_png(url, inv, assets_root)
        if data:
            payload['extra_images'].append({'role': str(label), 'data': data,
                                            'width': size and size[0], 'height': size and size[1]})
    # Escape HTML delimiters even inside JSON strings to prevent closing the script element.
    data = json.dumps(payload, ensure_ascii=True, default=str)
    data = data.replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
    template = Path(__file__).with_name('evidence_report.html').read_text(encoding='utf-8')
    # The id is regex-validated above; substitute it before the data so claim text is never rewritten.
    return template.replace('__REPORT_ID__', inv).replace('__REPORT_DATA__', data)
