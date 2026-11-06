# Improvement options and missing features

Analysis written after the tabbed-UI, model-selection and report work (Nov 2026). Each item says
what we observed, why it matters, and a rough size (S ≤ half a day, M ≈ 1–2 days, L ≥ 3 days).

## 1. Evidence quality (highest value)

| Option | Observed / why | Size |
|---|---|---|
| **Time series instead of two dates** | Verdicts rest on one before/after pair plus one corroboration scene. Seasonal noise (Bingham 45% persistence, Giga Berlin roof class wobbling 98% → 3%) would be far easier to judge from a monthly median stack and a change-point date. Also answers "*when* did it happen?", which claims often hinge on. | L |
| **Sentinel-1 radar fallback** | Optical imagery fails under persistent cloud (tropics, northern winters); SCL masking then forces "inconclusive". Sentinel-1 GRD sees through clouds and is good for construction, flooding and clearing. | L |
| **Tile mosaicking and larger AOIs** | One scene per date; AOIs over a tile edge lose coverage. Mosaicking same-day tiles and a 20 m mode would lift the 100 km² cap (see README FAQ). | M |
| **Built-up roof vs bare-soil margin** | BSI ~0.09 (roofs) vs ~0.12 (soil) is a thin threshold; misclassification is plausible on new sites. Add texture/edge density and a small labelled validation set per indicator. | M |
| **Validation harness** | No accuracy numbers exist. Score indicators against public reference data (e.g. Hansen/GLAD forest loss, MapBiomas, Global Surface Water, Dynamic World) on 30–50 sites and publish precision/recall per indicator. This is what turns heuristics into defensible evidence. | L |
| **Higher-resolution imagery option** | 10 m cannot resolve single buildings (< ~0.5 ha) — the "a building was illegally built here" use case often needs 3 m (Planet) or sub-metre commercial imagery, behind a provider interface like the model registry. | L (+ licensing) |
| **2022 archive gap** | `sentinel-2-c1-l2a` had almost no 2022 scenes at the sites tested. Fall back to the older `sentinel-2-l2a` collection (with its own offsets) or Copernicus Data Space for missing years. | M |

## 2. Agent and model

| Option | Observed / why | Size |
|---|---|---|
| **Separate vision and planning models** | Now one model per investigation. Letting a small fast text model plan while a stronger vision model interprets (or a cloud model only for the VLM step) would improve quality and cost. The registry already supports it; it's a second dropdown + context switch. | S |
| **Model evaluation page** | Run the 9 demo cases per model and compare claim routing, decisions and VLM consistency. Today we only know Qwen 3.5 4B is fast but over-confident. | M |
| **Disable reasoning on OpenAI-compatible servers** | Ollama's `/v1` endpoint ignored "no thinking" (~75 s vs ~25 s natively). Add per-model `extra_body` options in `config/models.json` (e.g. vLLM `chat_template_kwargs`). | S |
| **Claim decomposition** | Compound claims ("cleared forest *and* built a road") pick one indicator. Split into sub-claims, run several indicators, combine verdicts. | M |
| **Cost/latency budget per investigation** | Cloud models have per-token cost. Track tokens/time per step and show them; enforce a budget like the tool-call budget. | S |

## 3. Product and UX

| Option | Why | Size |
|---|---|---|
| **Draw-your-own AOI on the map** | Polygons instead of lat/lon/width; the backend already supports verified polygon grids. | M |
| **Compare-over-time slider / animation** | With a time series, a timelapse is the most persuasive visual for non-experts. | M |
| **Shareable read-only links and report signing** | Reports are files; a hash of the payload + server signature would let a recipient check it wasn't edited. | M |
| **Saved watches / alerts** | "Tell me if construction starts here" — periodic re-runs with notification. Turns a one-off check into monitoring. | L |
| **Accessibility and i18n** | Tabs are keyboard-accessible; full screen-reader pass and translated UI/report would help NGO and government users. | M |

## 4. Engineering and operations

| Option | Why | Size |
|---|---|---|
| **Job queue and multiple workers** | One in-process worker; geocoder rate-limit and caches assume a single process. Redis/RQ or Postgres-backed jobs for concurrent users. | M |
| **Authentication and per-user history** | Explicitly out of hackathon scope; needed before any shared deployment (API keys and paid models are server-side). | M |
| **Container image and GPU deployment recipe** | Native GDAL/PROJ setup was the main install friction; a Docker image plus an optional GPU host for Ollama/vLLM. | S–M |
| **Observability** | Structured logs per step, timing and provider errors, so slow or failing providers are visible. | S |
| **Cache management** | `data/cache` grows (~170 MB for 9 demos); add size limits/eviction and a shared object-store cache. | S |

## Suggested order

1. Validation harness (credibility), 2. separate vision/planning models (cheap quality win),
3. time series + change date (answers "when"), 4. Sentinel-1 fallback (coverage),
5. job queue + auth (only when it needs to be shared).
