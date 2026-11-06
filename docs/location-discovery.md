# Automatic location discovery

A claim can now start an investigation without `case_id` or `custom`. Geographic research runs as
`resolve_location` before claim interpretation and imagery tools. Existing case/manual requests keep
their previous behavior. Optional dates for automatic discovery come from years in the claim, or a
three-year baseline through today; actual image acquisition dates remain explicit.
Once a usable place is found, its coordinates and comparison dates are filled in and analysis
continues automatically. The geographic panel names the chosen place and shows the editable values.

## Configure the real providers

Copy `.env.example` to `.env` if needed and configure one search provider:

```dotenv
WEB_SEARCH_PROVIDER=tavily
TAVILY_API_KEY=your-key
LOCATION_USER_AGENT=Gandras/0.2 (your-operator-contact-URL)
```

Alternatively use `WEB_SEARCH_PROVIDER=brave` with `BRAVE_SEARCH_API_KEY`, or
`WEB_SEARCH_PROVIDER=searxng` with `SEARXNG_URL` (the instance must enable JSON search).
The search adapter performs actual HTTP requests; there is no fixture/demo-result fallback.
The configured Ollama/Qwen model handles entity extraction, query refinement and source assessment.
Without the model, search configuration or a usable location, discovery explains the failure and offers manual
entry instead of guessing. Credentials are server-side environment variables, never browser inputs.

Coordinates use [Photon](https://github.com/komoot/photon) by default, with no additional API key.
`PHOTON_URL` can point to a self-hosted instance. The public demo endpoint allows reasonable request
volumes and offers no availability guarantee; use your own service for larger deployments.
Requests are serialized to at most one per second and cached for 30 days. An explicitly configured
`NOMINATIM_URL` takes precedence over Photon.

Nominatim is configurable and deliberately has no default endpoint. For a small user-triggered deployment,
the developer may explicitly opt into `https://nominatim.openstreetmap.org` after reviewing the
[Nominatim usage policy](https://operations.osmfoundation.org/policies/nominatim/). The public service
requires an identifying User-Agent, visible OpenStreetMap attribution, an application-wide maximum of
one request per second, and caching. Do not use it for autocomplete, bulk/systematic queries or a
generic AI place-search service. The adapter serializes requests and caches results for 30 days; run
one application worker. Use a hosted or self-hosted geocoder for larger/multi-worker deployments.

Search contracts follow the [Tavily Search API](https://docs.tavily.com/documentation/api-reference/endpoint/search),
[Brave Web Search API](https://api-dashboard.search.brave.com/api-reference/web/search/get), and
[Nominatim Search API](https://nominatim.org/release-docs/latest/api/Search/).

## Evidence and uncertainty

- The model proposes geographic names/queries, never coordinate values. Coordinates are read from the
  geocoding response; country/address/site agreement is checked.
- Search stops after the first round that yields a source-backed geocoded candidate. Additional
  searches (default maximum three, hard-capped at five) happen only when nothing usable is found.
  Location lookup does not try to prove the claim's dates or outcome. Those dates come from the claim
  and are carried into the satellite comparison.
- Candidates are ranked by identity evidence, name, specificity, region and country agreement, then
  citation count. Ties retain provider order. The best match is selected automatically and disclosed;
  one source is enough to start sampling. A match without independent verification is labeled
  `approximate_location`, and uncertainty remains visible. Strong identity verification and published
  boundaries still require the original official/independent-source checks.
- Search snippets guide discovery but cannot verify the site. Source fetches are bounded and reject
  private/local URLs and private redirect destinations. PDFs and JavaScript-only pages are not currently
  interpreted; the agent must find accessible supporting sources or offer manual entry.
- An OSM polygon is not assumed to be a property boundary. An actual project GeoJSON must be found
  through a retrieved source/link, explicitly tied to the project by official-source evidence, valid,
  within the area limit, and consistent with the geocoded point. Only one Polygon/MultiPolygon is
  accepted (a single-feature collection is also accepted). Multiple unrelated parcels require manual
  review; large boundaries fall back to a provisional sample rather than being silently truncated.
- Verified polygons define the projected grid and mask both comparison and corroboration pixels.
  Provisional boxes are sampling windows. Every automatically discovered/user-confirmed provisional
  AOI forces a project-wide verdict to **inconclusive**, irrespective of measured change strength.
- Quoted geographic evidence, retrieval timestamps, query history, uncertainty, boundary provenance
  and geocoding records are persisted and included in the portable report.
- Multiple candidates do not block analysis. The selected place, coordinates, dates and alternatives
  are saved in `location_resolution` with `selection: "automatic"`. Users can correct coordinates/dates
  or select another saved candidate once the investigation is idle. If nothing usable is found,
  `location_unresolved` stops polling and opens the manual-entry form. Older `awaiting_location`
  investigations still expose their candidate-selection controls.

## API

```http
POST /investigations
Content-Type: application/json

{"claim":"The named restoration project in Brazil has gained vegetation since 2022."}
```

`GET /investigations/{id}` includes `location_resolution` with status, location, candidate list,
coordinates, bbox/geometry, evidence URLs/quotes, uncertainty and research attempts. Timeline entries
track extraction, searches, page reads, cross-checks, candidate examination and coordinate resolution.
`GET /health` exposes configuration readiness without API keys.

To confirm an offered candidate or provide a correction:

```http
POST /investigations/{id}/location
Content-Type: application/json

{"candidate_id":"location_1"}
```

Or send `{"custom":{"latitude":...,"longitude":...,"width_km":5,"before_date":"2022-01-01","after_date":"2025-01-01"}}`.
Only an idle investigation can be edited; queued/running workers return 409. All old imagery/verdict
references are cleared and analysis restarts for the new AOI. Geographic research history is retained.

## Validate with real services

```powershell
.\.venv\Scripts\python.exe scripts/discover_location.py "Apple reports that Project Alpha in Brazil planted nearly 15 million trees since 2022. Does satellite imagery show vegetation growth consistent with this claim?" --output data/location-research.json
```

This is a normal input, not a built-in case. Ambiguous names may select an approximate location;
the chosen place and source evidence must be reviewed by the user if incorrect.
Exit status 0 indicates resolved, 2 means manual entry is needed. No coordinates or
source URLs are hardcoded for this example.

Unit tests in `tests/test_location.py` use explicitly fictional fixtures and controlled HTTP contracts
for success, refinement, ambiguity, fabrication rejection, geocoder mismatch, source boundaries,
manual fallback, legacy compatibility and pipeline handoff. Production uses only real providers.
The polygon-grid test needs working native PROJ/GDAL libraries. The interactive map uses locally bundled Leaflet 1.9.4
with integrity checks and visible OSM attribution; if map assets are unavailable, a geographic map link
remains. Browser tiles follow normal caching and the
[OSM tile policy](https://operations.osmfoundation.org/policies/tiles/), without prefetching.
