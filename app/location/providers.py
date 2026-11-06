"""Real search, bounded public-source retrieval and cached, rate-limited Nominatim requests."""
from __future__ import annotations

import hashlib
import ipaddress
import json
import math
import socket
import threading
import time
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import httpx

from app.config import get_settings
from app.database import now


class ResearchError(RuntimeError):
    pass


def public_url(url: str) -> str:
    p = urlparse(url)
    if p.scheme not in ('http', 'https') or not p.hostname or p.username or p.password or p.port not in (None, 80, 443):
        raise ResearchError('Source URL is not a public HTTP resource.')
    try:
        addresses = socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == 'https' else 80))
        if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
            raise ResearchError('Private network sources are not permitted.')
    except OSError as exc:
        raise ResearchError('Source host could not be resolved.') from exc
    return url


class TextPage(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts, self.links, self.skip = [], [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'noscript', 'svg'):
            self.skip += 1
        if tag == 'a' and not self.skip:
            self.links.extend(v for k, v in attrs if k == 'href' and v)

    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'noscript', 'svg') and self.skip:
            self.skip -= 1

    def handle_data(self, text):
        if not self.skip:
            self.parts.append(text)


def fetch_source(url: str) -> dict:
    """No browser execution, cookies, local URLs, or unchecked redirect targets."""
    s = get_settings()
    limit = 1_500_000
    with httpx.Client(timeout=s.http_timeout_s, follow_redirects=False, trust_env=False,
                      headers={'User-Agent': s.location_user_agent}) as client:
        for _ in range(4):
            public_url(url)
            with client.stream('GET', url) as response:
                if response.is_redirect:
                    url = urljoin(url, response.headers.get('location', ''))
                    continue
                response.raise_for_status()
                chunks, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > limit:
                        raise ResearchError('Source exceeds the retrieval size limit.')
                    chunks.append(chunk)
                content = b''.join(chunks).decode('utf-8', errors='replace')
                content_type = response.headers.get('content-type', '')
                if 'json' in content_type or urlparse(url).path.lower().endswith(('.geojson', '.json')):
                    return {'url': url, 'text': content[:18000], 'links': [], 'json': json.loads(content), 'retrieved_at': now()}
                if 'html' not in content_type and 'text/plain' not in content_type:
                    raise ResearchError('Unsupported source format; use HTML, text or GeoJSON.')
                page = TextPage()
                page.feed(content)
                return {'url': url, 'text': ' '.join(' '.join(page.parts).split())[:18000],
                        'links': list(dict.fromkeys(urljoin(url, a) for a in page.links))[:150], 'retrieved_at': now()}
    raise ResearchError('Source redirected too many times.')


def search_web(query: str) -> list[dict]:
    s = get_settings()
    with httpx.Client(timeout=s.http_timeout_s, trust_env=False) as client:
        if s.web_search_provider == 'tavily':
            if not s.tavily_api_key:
                raise ResearchError('Set TAVILY_API_KEY in .env to enable automatic location research, or supply a location manually.')
            r = client.post('https://api.tavily.com/search', headers={'Authorization': 'Bearer ' + s.tavily_api_key},
                            json={'query': query[:400], 'max_results': 6, 'search_depth': 'advanced',
                                  'include_answer': False, 'include_raw_content': False})
            r.raise_for_status()
            return [{'url': x['url'], 'title': x.get('title', ''), 'snippet': x.get('content', '')[:2500]}
                    for x in r.json().get('results', []) if x.get('url')]
        if s.web_search_provider == 'brave':
            if not s.brave_search_api_key:
                raise ResearchError('Set BRAVE_SEARCH_API_KEY in .env to enable automatic location research, or supply a location manually.')
            r = client.get('https://api.search.brave.com/res/v1/web/search',
                           headers={'X-Subscription-Token': s.brave_search_api_key}, params={'q': query[:400], 'count': 6})
            r.raise_for_status()
            return [{'url': x['url'], 'title': x.get('title', ''), 'snippet': x.get('description', '')[:2500]}
                    for x in r.json().get('web', {}).get('results', []) if x.get('url')]
        if s.web_search_provider == 'searxng' and s.searxng_url:
            r = client.get(s.searxng_url.rstrip('/') + '/search', params={'q': query[:400], 'format': 'json'})
            r.raise_for_status()
            return [{'url': x['url'], 'title': x.get('title', ''), 'snippet': x.get('content', '')[:2500]}
                    for x in r.json().get('results', [])[:6] if x.get('url')]
        raise ResearchError('Configure a supported web search provider (tavily, brave or searxng).')


_geocode_lock = threading.Lock()
_last_geocode = 0.0


def _photon_results(data: dict, country_code: str) -> list[dict]:
    """Normalize Photon GeoJSON to the coordinate contract used by the resolver."""
    if not isinstance(data, dict) or not isinstance(data.get('features'), list):
        raise ResearchError('Unexpected geocoder response.')
    results = []
    for feature in data['features']:
        try:
            p = feature['properties']
            if p.get('countrycode', '').lower() != country_code.lower():
                continue
            if feature['geometry']['type'] != 'Point':
                continue
            lon, lat = map(float, feature['geometry']['coordinates'])
            if not math.isfinite(lat + lon) or not (-84 <= lat <= 84 and -180 <= lon <= 180):
                continue
            # Photon extents are west, north, east, south. A point has a zero-size extent.
            west, north, east, south = p.get('extent', [lon, lat, lon, lat])
            label = ', '.join(str(p[k]) for k in ('name', 'housenumber', 'street', 'city', 'state', 'country') if p.get(k))
            results.append({'lat': lat, 'lon': lon, 'name': p.get('name', ''), 'display_name': label,
                            'address': {'country_code': country_code.lower()},
                            'boundingbox': [south, north, west, east],
                            'addresstype': p.get('type') or p.get('osm_value'),
                            'osm_type': {'N': 'node', 'W': 'way', 'R': 'relation'}.get(p.get('osm_type')),
                            'osm_id': p.get('osm_id')})
        except (KeyError, TypeError, ValueError):
            continue
    return results


def geocode(query: str, country_code: str) -> list[dict]:
    """At most one outbound request/second for the app; durable cache, no autocomplete."""
    global _last_geocode
    s = get_settings()
    endpoint = s.nominatim_url or s.photon_url
    if not endpoint:
        raise ResearchError('Configure PHOTON_URL or NOMINATIM_URL, or enter coordinates manually.')
    key = hashlib.sha256(json.dumps([endpoint, query, country_code]).encode()).hexdigest()
    path = s.resolve(s.cache_dir) / 'geocoding' / (key + '.json')
    with _geocode_lock:
        if path.is_file() and time.time() - path.stat().st_mtime < 30 * 86400:
            return json.loads(path.read_text(encoding='utf-8'))
        time.sleep(max(0, 1.05 - (time.monotonic() - _last_geocode)))
        try:
            params = ({'q': query, 'format': 'jsonv2', 'addressdetails': 1,
                       'countrycodes': country_code.lower(), 'limit': 4, 'polygon_geojson': 1}
                      if s.nominatim_url else {'q': query, 'countrycode': country_code.upper(), 'limit': 4})
            r = httpx.get(endpoint.rstrip('/') + ('/search' if s.nominatim_url else '/api'), timeout=s.http_timeout_s,
                          headers={'User-Agent': s.location_user_agent},
                          params=params)
            r.raise_for_status()
        finally:
            _last_geocode = time.monotonic()
        result = r.json() if s.nominatim_url else _photon_results(r.json(), country_code)
        if not isinstance(result, list):
            raise ResearchError('Unexpected geocoder response.')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result), encoding='utf-8')
        return result
