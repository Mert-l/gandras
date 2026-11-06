"""Portable evidence report: offline, escaped, and embedding only validated local PNGs."""
import base64
import json
import re
from html.parser import HTMLParser

import pytest

from app.verification.report import render_evidence_report

INV = 'inv_abcdef0123'
PNG_1x1 = base64.b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==')
HOSTILE = '</script><img src=x onerror=alert(1)>'


class _Refs(HTMLParser):
    """Collects every static resource / link reference and inline script/style text."""

    def __init__(self):
        super().__init__()
        self.refs, self.tags, self.scripts, self.styles, self._in = [], [], [], [], None

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        for name, value in attrs:
            if name in ('src', 'href', 'srcset', 'poster', 'data', 'action', 'xlink:href'):
                self.refs.append((tag, name, value or ''))
        self._in = tag if tag in ('script', 'style') else None

    def handle_endtag(self, tag):
        self._in = None

    def handle_data(self, data):
        if self._in == 'script':
            self.scripts.append(data)
        elif self._in == 'style':
            self.styles.append(data)


def _parse(html):
    p = _Refs()
    p.feed(html)
    return p


def _payload(html):
    m = re.search(r'<script id="report-data" type="application/json">(.*?)</script>', html, re.S)
    assert m, 'report data block missing'
    return m[1], json.loads(m[1])


def _state(**extra):
    state = {
        'investigation_id': INV, 'claim': 'No forest was cleared here after 2020.', 'status': 'completed',
        'created_at': '2026-11-05T18:18:31+00:00', 'updated_at': '2026-11-05T18:18:45+00:00',
        'indicator': 'forest_clearing', 'requested_indicator': 'auto',
        'indicator_info': {'id': 'forest_clearing', 'name': 'Forest clearing'},
        'claim_interpretation': {'asserts': 'no_change', 'direction': 'decrease'},
        'location': {'name': 'Test site', 'latitude': -9.54, 'longitude': -65.93, 'width_km': 5},
        'verdict': {'status': 'contradicted', 'confidence': 'medium', 'summary': 'Measured clearing.',
                    'observed': ['x'], 'inferred': [], 'not_established': ['legality']},
        'measurements': {'evidence_ha': 12.5, 'regions': [{'id': 1, 'area_ha': 12.5, 'bbox_px': [1, 2, 3, 4]}]},
        'methodology': {'grid': {'width': 1, 'height': 1, 'resolution_m': 10}},
        'sources': [{'type': 'satellite', 'role': 'before', 'scene_id': 'S2A_X', 'url': 'https://example.org/stac/S2A_X'}],
        'imagery': {'before': {'url': f'/assets/{INV}/before.png', 'scene_id': 'S2A_X', 'acquired_at': '2020-05-19T17:25:23Z'},
                    'after': {'url': f'/assets/{INV}/after.png'}, 'overlay_url': f'/assets/{INV}/overlay.png',
                    'extra': {'NDVI change': f'/assets/{INV}/ndvi_delta.png'}},
        'steps': [{'index': 1, 'action': 'interpret_claim', 'status': 'completed', 'decided_by': 'llm',
                   'summary': 'Read claim.', 'started_at': '2026-11-05T18:18:31+00:00',
                   'details': {'secret_internal': 'should not be embedded'}, 'reason': 'internal'}],
        'visual_analysis': {'status': 'ok', 'model': 'qwen3.5:4b', 'analysis': {'summary': 'Clearing visible.'}},
    }
    state.update(extra)
    return state


@pytest.fixture
def assets(tmp_path):
    folder = tmp_path / INV
    folder.mkdir()
    for name in ('before.png', 'after.png', 'overlay.png', 'ndvi_delta.png'):
        (folder / name).write_bytes(PNG_1x1)
    return tmp_path


def test_report_makes_no_external_requests(assets):
    html = render_evidence_report(_state(), assets)
    doc = _parse(html)
    for tag, attr, value in doc.refs:
        assert value.startswith('#') or value.startswith('data:'), (tag, attr, value)
    assert 'link' not in doc.tags and 'iframe' not in doc.tags and 'object' not in doc.tags
    assert not re.search(r'<script[^>]*\ssrc=', html, re.I)
    css = '\n'.join(doc.styles)
    assert '@import' not in css and not re.search(r'url\(', css, re.I)
    # Runtime-created images only ever take embedded PNG data URIs; links only http(s).
    assert "startsWith('data:image/png;base64,')" in html
    assert r"/^https?:\/\//i.test(u)" in html
    assert 'innerHTML' not in html and 'outerHTML' not in html and 'document.write' not in html


def test_report_escapes_hostile_json(assets):
    html = render_evidence_report(_state(claim=HOSTILE, location={'name': HOSTILE + '&<!--'}), assets)
    raw, data = _payload(html)
    assert data['claim'] == HOSTILE
    assert data['location']['name'] == HOSTILE + '&<!--'
    assert '</script' not in raw.lower() and '<' not in raw and '>' not in raw and '&' not in raw
    assert HOSTILE not in html
    assert html.count('</script>') == 2  # the data block and the renderer, nothing injected


def test_report_payload_fields_and_trimming(assets):
    model = {'id': 'm1', 'label': 'Local model', 'provider': 'ollama', 'model': 'qwen', 'location': 'local',
             'vision': True, 'api_key': 'sk-should-not-leak'}
    html = render_evidence_report(_state(model=model), assets)
    _, data = _payload(html)
    assert data['model'] == {k: v for k, v in model.items() if k != 'api_key'}
    assert 'sk-should-not-leak' not in html
    assert data['steps'] == [{'index': 1, 'action': 'interpret_claim', 'status': 'completed', 'decided_by': 'llm',
                              'summary': 'Read claim.', 'started_at': '2026-11-05T18:18:31+00:00'}]
    assert 'should not be embedded' not in html
    for key in ('indicator', 'indicator_info', 'claim_interpretation', 'requested_indicator', 'visual_analysis',
                'corroboration', 'report_generated_at'):
        assert key in data
    assert re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z', data['report_generated_at'])
    assert [i['role'] for i in data['images']] == ['Before', 'After', 'Change overlay']
    assert all(i['data'].startswith('data:image/png;base64,') and (i['width'], i['height']) == (1, 1)
               for i in data['images'])
    assert data['extra_images'][0]['role'] == 'NDVI change' and data['extra_images'][0]['data']
    assert 'url' not in json.dumps(data['images']) and '/assets/' not in html
    assert f'Report reference {INV}' in html and 'Satellite Evidence Report' in html
    assert 'bbox_px' in html and 'aria-pressed' in html


def test_model_absent_or_malformed(assets):
    for model in (None, 'gpt', ['x'], {'api_key': 'x'}):
        _, data = _payload(render_evidence_report(_state(model=model), assets))
        assert data['model'] is None


@pytest.mark.parametrize('url', [
    '/assets/../../etc/passwd',
    f'/assets/{INV}/../inv_ffffffffff/before.png',
    '/assets/inv_ffffffffff/before.png',
    f'/assets/{INV}/BEFORE.png',
    f'/assets/{INV}/before.png?x=1',
    f'/assets/{INV}/missing.png',
    'https://untrusted.example/before.png',
    'file:///etc/passwd',
    None, 42,
])
def test_only_validated_local_images_are_embedded(tmp_path, url):
    for inv in (INV, 'inv_ffffffffff'):
        (tmp_path / inv).mkdir()
        (tmp_path / inv / 'before.png').write_bytes(PNG_1x1)
    state = _state(imagery={'before': {'url': url}, 'after': {'url': url}, 'overlay_url': url, 'extra': {'Map': url}})
    html = render_evidence_report(state, tmp_path)
    _, data = _payload(html)
    assert all(i['data'] is None for i in data['images'])
    assert data['extra_images'] == []
    if isinstance(url, str):
        assert url not in html


def test_symlink_outside_assets_root_is_not_embedded(tmp_path):
    outside = tmp_path / 'outside.png'
    outside.write_bytes(PNG_1x1)
    root = tmp_path / 'assets'
    (root / INV).mkdir(parents=True)
    (root / INV / 'before.png').symlink_to(outside)
    _, data = _payload(render_evidence_report(_state(), root))
    assert data['images'][0]['data'] is None


def test_minimal_state_renders(tmp_path):
    html = render_evidence_report({'investigation_id': INV, 'claim': 'Something happened.', 'status': 'queued'}, tmp_path)
    _, data = _payload(html)
    assert data['claim'] == 'Something happened.'
    assert data['images'] and all(i['data'] is None for i in data['images'])
    assert data['steps'] == [] and data['sources'] == [] and data['model'] is None and data['extra_images'] == []
    assert '__REPORT_DATA__' not in html and '__REPORT_ID__' not in html


def test_failed_state_with_location_research(tmp_path):
    state = {'investigation_id': INV, 'claim': 'Taxes were paid.', 'status': 'location_unresolved',
             'error': {'code': 'LOCATION_UNRESOLVED', 'message': 'No reliable project location was found.'},
             'imagery': None, 'verdict': None, 'location': None,
             'location_resolution': {'status': 'unresolved', 'sources': [
                 {'id': 1, 'url': 'https://example.org/a', 'title': 'A'}, 'not-a-dict']}}
    _, data = _payload(render_evidence_report(state, tmp_path))
    assert data['error']['code'] == 'LOCATION_UNRESOLVED'
    assert data['sources'] == [{'type': 'geographic', 'id': 1, 'url': 'https://example.org/a', 'title': 'A'}]


def test_placeholder_tokens_in_claim_are_not_rewritten(tmp_path):
    claim = 'literal __REPORT_ID__ and __REPORT_DATA__ text'
    _, data = _payload(render_evidence_report({'investigation_id': INV, 'claim': claim, 'status': 'completed'}, tmp_path))
    assert data['claim'] == claim


@pytest.mark.parametrize('inv', ['inv_123', '../inv_abcdef0123', 'inv_ABCDEF0123', None])
def test_invalid_investigation_id_rejected(tmp_path, inv):
    with pytest.raises(ValueError):
        render_evidence_report({'investigation_id': inv, 'claim': 'x', 'status': 'completed'}, tmp_path)
