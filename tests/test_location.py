"""Deterministic provider/LLM contract fixtures; production always performs real HTTP research."""
from copy import deepcopy

import httpx
import pytest
from fastapi.testclient import TestClient

from app import database
from app.agent.state import InvestigationState
from app.config import get_settings
from app.location import providers, resolver
from app.location.models import Clues, Research
from app.main import app


@pytest.fixture
def research_fixture(monkeypatch):
    # Fictional unit-test site. These values are never shipped as runtime/demo answers.
    text = 'North Field is the North Field project operated by Test Forestry in Brazil, Minas Gerais. The property covers 400 hectares.'
    sources = [
        {'url': 'https://operator.example/project', 'text': text, 'links': [], 'retrieved_at': database.now()},
        {'url': 'https://research.example/site', 'text': text, 'links': [], 'retrieved_at': database.now()},
    ]
    proposal = {'name': 'North Field', 'project': 'North Field', 'operator': 'Test Forestry',
                'country': 'Brazil', 'country_code': 'br', 'region': 'Minas Gerais',
                'geocode_query': 'North Field, Minas Gerais, Brazil', 'specific_site': True, 'identity_consistent': True,
                'citations': [{'source_id': i+1, 'quote': text.split(' The property')[0],
                               'supports': ['project', 'operator', 'country', 'region', 'location'],
                               'source_quality': quality} for i, quality in enumerate(['official', 'reputable'])]}
    clue = Clues(projects=['North Field'], organizations=['Test Forestry'], countries=['Brazil'], queries=['North Field Brazil location'])
    state = {'queries': [], 'proposals': [proposal], 'results': [
        {'lat': '-20.1', 'lon': '-44.2', 'display_name': 'North Field, Minas Gerais, Brazil',
         'address': {'country_code': 'br'}, 'boundingbox': ['-20.11', '-20.09', '-44.21', '-44.19'],
         'addresstype': 'farm', 'osm_type': 'way', 'osm_id': 123}]}

    def chat(system, data, schema):
        if schema is Clues:
            return clue
        return Research(candidates=deepcopy(state['proposals']), next_queries=['North Field Test Forestry property boundary', 'North Field registry Brazil'])

    def search(query):
        state['queries'].append(query)
        return [{'url': x['url'], 'title': 'Test source'} for x in sources]

    monkeypatch.setattr(resolver, 'chat', chat)
    monkeypatch.setattr(providers, 'search_web', search)
    monkeypatch.setattr(providers, 'fetch_source', lambda url: deepcopy(next(s for s in sources if s['url'] == url)))
    monkeypatch.setattr(providers, 'geocode', lambda *args: deepcopy(state['results']))
    state.update(sources=sources, proposal=proposal, claim='Test Forestry planted trees at North Field in Brazil since 2022.')
    return state


def test_resolved_with_real_source_quotes_and_geocoder_contract(research_fixture):
    f = research_fixture
    result = resolver.resolve(f['claim'])
    assert result['status'] == 'resolved'
    assert len(f['queries']) == 1
    assert result['location']['latitude'] == -20.1
    assert result['location']['boundary_status'] == 'provisional'
    assert result['location']['geometry'] is None
    assert len(result['location']['evidence_urls']) == 2
    assert result['location']['bbox'][0] < -44.2 < result['location']['bbox'][2]


def test_multiple_matches_select_best_and_continue(research_fixture):
    f = research_fixture
    alternative = deepcopy(f['results'][0])
    alternative.update(lat='-20.2', boundingbox=['-20.21', '-20.19', '-44.21', '-44.19'])
    f['results'].append(alternative)
    result = resolver.resolve(f['claim'])
    assert result['status'] == 'resolved'
    assert result['location']['latitude'] == -20.1
    assert result['selection'] == 'automatic'
    assert len(result['candidates']) == 2


def test_unresolved_refines_queries(research_fixture):
    f = research_fixture
    f['proposals'] = []
    result = resolver.resolve(f['claim'])
    assert result['status'] == 'unresolved' and not result['candidates']
    assert len(f['queries']) == get_settings().location_search_attempts
    assert 'manually' in result['explanation']


def test_fabricated_citations_never_verify(research_fixture):
    f = research_fixture
    for c in f['proposal']['citations']:
        c['quote'] = 'North Field is at a location that was never in the retrieved documents.'
    result = resolver.resolve(f['claim'])
    assert result['status'] == 'resolved'
    assert result['location']['verification_status'] == 'approximate_location'
    assert all('never in the retrieved' not in c['quote'] for c in result['location']['evidence'])
    assert all(c['source_quality'] == 'unknown' for c in result['location']['evidence'])


def test_candidate_name_absent_from_sources_is_rejected(research_fixture):
    f = research_fixture
    f['proposal']['name'] = 'Invented School'
    f['proposal']['citations'] = []
    assert resolver.resolve(f['claim'])['status'] == 'unresolved'


def test_country_mismatch_rejected(research_fixture):
    f = research_fixture
    f['results'][0]['address']['country_code'] = 'cl'
    assert resolver.resolve(f['claim'])['status'] == 'unresolved'


def test_city_match_continues_with_approximate_location(research_fixture):
    f = research_fixture
    f['results'][0]['addresstype'] = 'city'
    result = resolver.resolve(f['claim'])
    assert result['status'] == 'resolved'
    assert result['location']['verification_status'] == 'approximate_location'
    assert result['location']['boundary_status'] == 'provisional'


def test_osm_polygon_is_not_property_boundary(research_fixture):
    f = research_fixture
    f['results'][0]['geojson'] = {'type': 'Polygon', 'coordinates': [[[-44.21,-20.11],[-44.19,-20.11],[-44.19,-20.09],[-44.21,-20.11]]]}
    result = resolver.resolve(f['claim'])
    assert result['location']['geometry'] is None
    assert result['location']['boundary_status'] == 'provisional'


def test_no_llm_does_not_guess_location():
    result = resolver.resolve('Trees grew somewhere', use_llm=False)
    assert result['status'] == 'unresolved'
    assert result['location'] is None and not result['attempts']


def test_claim_only_creation_and_manual_fallback(monkeypatch):
    from app.services import investigations as svc
    submitted = []
    monkeypatch.setattr(svc._executor, 'submit', lambda fn, inv: submitted.append(inv))
    client = TestClient(app)
    r = client.post('/investigations', json={'claim': 'Trees grew at North Field in Brazil since 2022.'})
    assert r.status_code == 202
    inv = r.json()['investigation_id']
    raw = database.load(inv)
    assert raw['location'] is None and raw['temporal_scope']['before_target'] == '2022-01-01'
    raw.update(status='location_unresolved', location_resolution={'status': 'unresolved', 'candidates': []})
    database.save(raw)
    manual = {'latitude': -20.1, 'longitude': -44.2, 'width_km': 3,
              'before_date': '2022-01-01', 'after_date': '2025-01-01'}
    resumed = client.post(f'/investigations/{inv}/location', json={'custom': manual})
    assert resumed.status_code == 202 and submitted == [inv, inv]
    raw = database.load(inv)
    assert raw['location']['latitude'] == -20.1 and raw['location']['boundary_status'] == 'provisional'
    assert raw['location_resolution']['verification_status'] == 'user_supplied'
    assert client.post(f'/investigations/{inv}/location', json={'custom': manual}).status_code == 409


def test_candidate_confirmation_uses_saved_candidate_only(monkeypatch, research_fixture):
    from app.services import investigations as svc
    monkeypatch.setattr(svc._executor, 'submit', lambda *args: None)
    st = svc.create('auto', research_fixture['claim'], None, None, {})
    result = resolver.resolve(st.claim)
    st.status = 'awaiting_location'
    st.location_resolution = {**result, 'status': 'ambiguous', 'location': None}
    st.imagery = {'before': {'url': 'stale'}}
    database.save(st.model_dump())
    client = TestClient(app)
    assert client.post(f'/investigations/{st.investigation_id}/location', json={'candidate_id': 'invented'}).status_code == 400
    r = client.post(f'/investigations/{st.investigation_id}/location', json={'candidate_id': result['candidates'][0]['id']})
    assert r.status_code == 202
    saved = database.load(st.investigation_id)
    assert saved['imagery'] is None and saved['verdict'] is None
    assert saved['location_resolution']['verification_status'] == 'user_confirmed'
    assert saved['location_resolution']['selection'] == 'user'


def test_location_tool_persists_research_and_waits(research_fixture):
    from app.agent.location import resolve_location
    st = InvestigationState(investigation_id='inv_1234567890', mode='auto', claim=research_fixture['claim'])
    research_fixture['proposals'] = []
    saved = []
    assert not resolve_location(st, lambda: saved.append(st.model_dump()))
    assert st.status == 'location_unresolved'
    assert st.steps[0].action == 'resolve_location'
    assert any(step.action == 'search_location' for step in st.steps)
    assert saved and all(step.status != 'running' for step in st.steps)


def test_runner_discovers_before_imagery_and_stops_when_ambiguous(research_fixture):
    from app.agent.orchestrator import Runner
    research_fixture['proposals'] = []
    st = InvestigationState(investigation_id='inv_1234567890', mode='auto', claim=research_fixture['claim'],
                            temporal_scope={'before_target': '2022-01-01', 'after_target': '2025-01-01'})
    result = Runner(st).run()
    assert result.status == 'location_unresolved'
    assert result.selected_scenes == {} and result.verdict is None


def test_search_missing_configuration_is_explicit(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, 'web_search_provider', 'tavily')
    monkeypatch.setattr(s, 'tavily_api_key', '')
    with pytest.raises(providers.ResearchError, match='TAVILY_API_KEY'):
        providers.search_web('project location')


def test_public_source_url_rejects_private_network():
    for url in ['http://127.0.0.1/private', 'file:///etc/passwd', 'http://169.254.169.254/latest/meta-data']:
        with pytest.raises(providers.ResearchError):
            providers.public_url(url)


def test_search_provider_contracts(monkeypatch):
    requests = []
    def handler(request):
        requests.append(request)
        if 'brave' in request.url.host:
            return httpx.Response(200, json={'web': {'results': [{'url': 'https://site.example', 'title': 'Site', 'description': 'Location'}]}})
        return httpx.Response(200, json={'results': [{'url': 'https://site.example', 'title': 'Site', 'content': 'Location'}]})
    original = httpx.Client
    monkeypatch.setattr(providers.httpx, 'Client', lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    s = get_settings()
    for provider in ['tavily', 'brave', 'searxng']:
        monkeypatch.setattr(s, 'web_search_provider', provider)
        monkeypatch.setattr(s, 'tavily_api_key', 'test-key')
        monkeypatch.setattr(s, 'brave_search_api_key', 'test-key')
        monkeypatch.setattr(s, 'searxng_url', 'https://search.example')
        assert providers.search_web('North Field')[0]['url'] == 'https://site.example'
    assert requests[0].headers['authorization'] == 'Bearer test-key'
    assert requests[1].headers['x-subscription-token'] == 'test-key'


def test_geocoder_cache_country_filter_and_user_agent(monkeypatch, tmp_path):
    requests = []
    def get(url, **kwargs):
        requests.append(kwargs)
        return httpx.Response(200, json=[{'lat': '1', 'lon': '2'}], request=httpx.Request('GET', url))
    s = get_settings()
    monkeypatch.setattr(s, 'nominatim_url', 'https://geo.example')
    monkeypatch.setattr(s, 'cache_dir', tmp_path)
    monkeypatch.setattr(providers.httpx, 'get', get)
    first = providers.geocode('North Field Brazil', 'BR')
    assert providers.geocode('North Field Brazil', 'BR') == first and len(requests) == 1
    assert requests[0]['params']['countrycodes'] == 'br'
    assert requests[0]['headers']['User-Agent'].startswith('Gandras/')


def test_published_boundary_geometry_validation():
    document = {'url': 'https://operator.example/boundary.geojson', 'json': {'type': 'Polygon',
                'coordinates': [[[-44.201,-20.101],[-44.199,-20.101],[-44.199,-20.099],[-44.201,-20.099],[-44.201,-20.101]]]}}
    result = resolver._boundary(document, None)
    assert result['boundary_status'] == 'verified' and result['geometry']['type'] == 'Polygon'
    document['json'] = {'type': 'FeatureCollection', 'features': []}
    assert resolver._boundary(document, None) is None


def test_verified_location_continues_to_existing_pipeline(research_fixture, monkeypatch):
    import sys
    import types
    import app.agent
    from app.agent.orchestrator import Runner
    fake_tools = types.ModuleType('app.agent.tools')
    monkeypatch.setitem(sys.modules, 'app.agent.tools', fake_tools)
    monkeypatch.setattr(app.agent, 'tools', fake_tools, raising=False)
    steps = []
    def interpret(runner):
        assert runner.st.location['verification_status'] == 'verified_location'
        steps.append('interpret')
        return True
    def allowed(st, retries):
        assert st.location['latitude'] == -20.1
        steps.append('satellite_planning')
        return ['finalize']
    monkeypatch.setattr(Runner, '_interpret_claim', interpret)
    monkeypatch.setattr('app.agent.planner.allowed_actions', allowed)
    monkeypatch.setattr(Runner, '_finalize', lambda *args, **kwargs: steps.append('finalize'))
    st = InvestigationState(investigation_id='inv_1234567890', mode='auto', claim=research_fixture['claim'])
    result = Runner(st).run()
    assert result.status == 'completed'
    assert steps == ['interpret', 'satellite_planning', 'finalize']
    assert result.location_resolution['status'] == 'resolved'


def test_provisional_scope_cannot_settle_project_claim():
    from app.location.scope import constrain_verdict
    confident = {'status': 'supported', 'confidence': 'high', 'summary': 'Changed'}
    assert constrain_verdict(confident, {'boundary_status': 'provisional'}, {'status': 'resolved'})['status'] == 'inconclusive'
    assert constrain_verdict(confident, {'boundary_status': 'verified'}, {'status': 'resolved'}) == confident
    # Existing manually supplied case workflows retain their original scope behavior.
    assert constrain_verdict(confident, {}, None) == confident


def test_legacy_cases_and_manual_coordinates_remain_supported(monkeypatch):
    from app.services import investigations as svc
    monkeypatch.setattr(svc._executor, 'submit', lambda *args: None)
    client = TestClient(app)
    for payload in [
        {'claim': 'Trees changed at this property.', 'case_id': 'forest_amazonas_control'},
        {'claim': 'Trees changed at this property.', 'custom': {'latitude': -20, 'longitude': -44, 'width_km': 5,
          'before_date': '2020-01-01', 'after_date': '2024-01-01'}},
    ]:
        response = client.post('/investigations', json=payload)
        assert response.status_code == 202
        st = database.load(response.json()['investigation_id'])
        assert st['location'] is not None and st['location_resolution'] is None
    payload['case_id'] = 'forest_amazonas_control'
    assert client.post('/investigations', json=payload).status_code == 422


def test_untrusted_boundary_url_not_fetched(research_fixture):
    f = research_fixture
    f['proposal'].update(boundary_url='https://invented.example/boundary.geojson', boundary_is_project=True)
    for citation in f['proposal']['citations']:
        citation['supports'].append('boundary')
    result = resolver.resolve(f['claim'])
    assert result['location']['boundary_status'] == 'provisional'


def test_source_verified_polygon_is_used(research_fixture, monkeypatch):
    f = research_fixture
    url = 'https://operator.example/boundary.geojson'
    f['sources'][0]['links'] = [url]
    f['proposal'].update(boundary_url=url, boundary_is_project=True)
    f['proposal']['citations'][0]['supports'].append('boundary')
    fetch = providers.fetch_source
    def source(target):
        if target == url:
            return {'url': url, 'json': {'type': 'Polygon', 'coordinates': [[
                [-44.201,-20.101],[-44.199,-20.101],[-44.199,-20.099],[-44.201,-20.099],[-44.201,-20.101]]]}}
        return fetch(target)
    monkeypatch.setattr(providers, 'fetch_source', source)
    result = resolver.resolve(f['claim'])
    assert result['location']['boundary_status'] == 'verified'
    assert result['location']['boundary_source'] == url


def test_polygon_analysis_grid_masks_outside_boundary():
    pytest.importorskip('pyproj', exc_type=ImportError)
    pytest.importorskip('rasterio', exc_type=ImportError)
    from app.satellite.aoi import AOI
    geometry = {'type': 'Polygon', 'coordinates': [[[-44.201,-20.101],[-44.199,-20.101],[-44.201,-20.099],[-44.201,-20.101]]]}
    aoi = AOI(lat=-20.1, lon=-44.2, width_m=1000, geometry=geometry)
    grid = aoi.grid(10)
    mask = aoi.mask(grid)
    assert mask.any() and not mask.all()
    assert mask.shape == (grid.height, grid.width)


def test_claim_country_mismatch_is_disclosed_on_selected_location(research_fixture, monkeypatch):
    f = research_fixture
    chat = resolver.chat
    def changed(system, data, schema):
        if schema is Clues:
            return Clues(projects=['North Field'], organizations=['Test Forestry'], countries=['Chile'], queries=['North Field Chile'])
        return chat(system, data, schema)
    monkeypatch.setattr(resolver, 'chat', changed)
    result = resolver.resolve(f['claim'].replace('Brazil', 'Chile'))
    assert result['status'] == 'resolved'
    assert result['location']['verification_status'] == 'approximate_location'
    assert any('country' in note.lower() for note in result['location']['uncertainty'])


def test_empty_first_search_refines_then_resolves(research_fixture, monkeypatch):
    f = research_fixture
    original = providers.search_web
    calls = []
    def search(query):
        calls.append(query)
        return [] if len(calls) == 1 else original(query)
    monkeypatch.setattr(providers, 'search_web', search)
    result = resolver.resolve(f['claim'])
    assert len(calls) >= 2 and calls[0] != calls[1]
    assert result['status'] == 'resolved'


def test_two_operator_subdomains_are_not_independent(research_fixture):
    f = research_fixture
    f['sources'][1]['url'] = 'https://research.operator.example/site'
    result = resolver.resolve(f['claim'])
    assert result['status'] == 'resolved'
    assert result['location']['verification_status'] == 'approximate_location'


def test_exact_site_ranked_above_city(research_fixture):
    f = research_fixture
    city = deepcopy(f['results'][0])
    city.update(lat='-20.2', display_name='Minas Gerais, Brazil', addresstype='city',
                boundingbox=['-20.21', '-20.19', '-44.21', '-44.19'])
    f['results'].insert(0, city)
    result = resolver.resolve(f['claim'])
    assert result['status'] == 'resolved'
    assert result['location']['latitude'] == -20.1


def test_site_alias_ranked_above_broad_place_proposal(research_fixture):
    f = research_fixture
    # A local-language geocoder label may not literally match the English site name.
    f['results'][0]['display_name'] = 'Campo Norte, Minas Gerais, Brasil'
    f['proposal']['identity_consistent'] = False
    broad = deepcopy(f['proposal'])
    broad.update(name='Minas Gerais', specific_site=False, identity_consistent=False)
    f['proposals'].insert(0, broad)
    result = resolver.resolve(f['claim'])
    assert result['location']['name'] == 'North Field'


def test_single_source_is_enough_to_start_sampling(research_fixture):
    f = research_fixture
    f['proposal']['citations'] = f['proposal']['citations'][:1]
    result = resolver.resolve(f['claim'])
    assert result['status'] == 'resolved'
    assert result['location']['verification_status'] == 'approximate_location'
    assert len(f['queries']) == 1


def test_failed_candidate_does_not_hide_usable_alternative(research_fixture, monkeypatch):
    f = research_fixture
    failing = deepcopy(f['proposal'])
    failing['geocode_query'] = 'Unavailable place'
    f['proposals'].insert(0, failing)
    geocode = providers.geocode
    def lookup(query, country):
        if query == 'Unavailable place':
            raise providers.ResearchError('Temporary geocoder failure')
        return geocode(query, country)
    monkeypatch.setattr(providers, 'geocode', lookup)
    result = resolver.resolve(f['claim'])
    assert result['status'] == 'resolved'
    assert result['location']['latitude'] == -20.1


def test_auto_location_handoff_preserves_claim_dates(research_fixture, monkeypatch):
    from app.agent.location import resolve_location
    from app.services import investigations as svc
    monkeypatch.setattr(svc._executor, 'submit', lambda *args: None)
    research_fixture['proposal']['citations'] = research_fixture['proposal']['citations'][:1]
    st = svc.create('auto', 'Test Forestry planted trees at North Field in Brazil from 2020 to 2023.', None, None, {})
    assert resolve_location(st, lambda: database.save(st.model_dump()))
    saved = database.load(st.investigation_id)
    assert saved['location']['latitude'] == -20.1
    assert saved['temporal_scope']['before_target'] == '2020-01-01'
    assert saved['temporal_scope']['after_target'] == '2023-12-31'
    assert saved['location_resolution']['selection'] == 'automatic'
    assert saved['error'] is None


def test_default_photon_geocodes_without_extra_setup(monkeypatch, tmp_path):
    s = get_settings()
    monkeypatch.setattr(s, 'nominatim_url', '')
    monkeypatch.setattr(s, 'cache_dir', tmp_path)
    requests = []
    def get(url, **kwargs):
        requests.append((url, kwargs))
        return httpx.Response(200, request=httpx.Request('GET', url), json={'features': [
            {'type': 'Feature', 'geometry': {'type': 'Point', 'coordinates': [-44.2, -20.1]},
             'properties': {'name': 'North Field', 'state': 'Minas Gerais', 'country': 'Brazil',
                            'countrycode': 'BR', 'osm_type': 'W', 'osm_id': 123, 'osm_value': 'farm'}},
            {'type': 'Feature', 'geometry': {'type': 'Point', 'coordinates': [-70, -30]},
             'properties': {'name': 'North Field', 'countrycode': 'CL'}},
        ]})
    monkeypatch.setattr(providers.httpx, 'get', get)
    result = providers.geocode('North Field Brazil', 'br')
    assert len(result) == 1
    assert float(result[0]['lat']) == -20.1 and float(result[0]['lon']) == -44.2
    assert result[0]['osm_type'] == 'way'
    assert result[0]['address']['country_code'] == 'br'
    assert 'North Field' in result[0]['display_name']
    assert requests[0][0] == 'https://photon.komoot.io/api'
    assert requests[0][1]['params']['countrycode'] == 'BR'
    assert providers.geocode('North Field Brazil', 'br') == result
    assert len(requests) == 1
