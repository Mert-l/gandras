# The Earth Has Receipts

An agentic AI investigator that checks claims about the physical world against real Sentinel-2
imagery. A claim is mapped to an **indicator** (forest clearing, mining/earthworks, construction,
surface water, fire, vegetation cover) or, if none fits, to a **generic surface-change fallback**.

The agent decides what to investigate, geospatial code measures, a local Qwen vision model
interprets, and a rule-based verification engine decides what the evidence supports. Reports
separate what was **observed** from what is **inferred** and what is **not established**.

See [docs/architecture.md](docs/architecture.md) and [docs/methodology.md](docs/methodology.md).

## Indicators

| Indicator | Example claim | Evidence (red) |
|---|---|---|
| Forest clearing (ForestCheck) | No forest was cleared here after 2020 | canopy before + NDVI drop + bare-soil rise |
| Mining & earthworks (MineWatch) | The mine stopped expanding after 2019 | robust multispectral surface change |
| Construction & built-up (BuildCheck) | No construction took place here after 2019 / A building was illegally built here | new built-like surface (roofs, paving); cleared ground shown amber, not counted |
| Surface water (WaterCheck) | The reservoir has not shrunk since 2019 | water appeared / disappeared (MNDWI) |
| Fire & burn scars (BurnCheck) | No fire burned here in summer 2021 | dNBR burn scars |
| Vegetation cover | The restoration project re-vegetated this area | NDVI gain / loss |
| General surface change (fallback) | Nothing changed at this site | any surface change; verdict limited to supported / inconclusive |

Pick an indicator in the UI or leave **Auto-detect**. Construction claims are tested only for
whether new built surface appeared: completion, permits and legality are never established, and
single small buildings (< ~0.5 ha) are below the 10 m resolution.

## Demo cases (real imagery; results from the cached runs, auto-detected indicator)

| Case | Claim | Verdict |
|---|---|---|
| `forest_sw_amazon_frontier` | No forest was cleared after 2020 | contradicted · high — 141 ha probable clearing, 98% persists |
| `forest_amazonas_control` | Forest intact since 2020 | supported · medium — 0 ha |
| `mine_quebrada_blanca` | Stopped expanding after 2019 | contradicted · medium — 56.8 ha disturbance, 86% persists |
| `mine_bingham_canyon` | Not expanded since 2019 | inconclusive · medium — 68.9 ha change, 45% persists |
| `build_giga_texas` | No large construction after 2020 | contradicted · medium — 49.2 ha new built surface (+62.6 ha graded ground, amber) |
| `build_giga_berlin` | The factory was completed by 2023 | supported · medium — 50.1 ha new built surface; completion itself not established; roof class unstable across dates |
| `build_loecknitztal_control` | No construction in this protected area after 2019 | supported · medium — 0 ha |
| `water_lake_mead_lv_bay` | Shoreline has not receded since 2019 | contradicted · high — 69.6 ha water lost (940 → 811 ha) |
| `burn_caldor_grizzly_flats` | No fire burned in summer 2021 | contradicted · medium — 1,658 ha burn scar |

Generic fallback example: "Nothing has changed at this site since 2019." on the Quebrada Blanca case
→ general surface change 56.8 ha → inconclusive (low).

Note: `sentinel-2-c1-l2a` has almost no 2022 scenes in the regions tested; avoid 2022 dates.

## Setup

Requires Python 3.12 and [Ollama](https://ollama.com).

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
ollama pull qwen3.5:4b
python scripts/check_environment.py
```

## Pre-warm the demo cache (before presenting)

```bash
python scripts/prewarm_cache.py
```

Runs all nine demo cases once (~5 min first time). Afterwards they replay from cache in
~10 s each and are marked "Replayed from cache" in the UI. Cached data are the same real scenes.

## Run

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

- App: http://localhost:8000
- API docs: http://localhost:8000/docs

Use a single worker (the default); investigations run in one background thread.

## Test

```bash
pytest -q
```

Live VLM test (needs Ollama): `RUN_LIVE_VLM=1 pytest -q tests/test_vlm.py`.

## Useful scripts

| Script | Purpose |
|---|---|
| `scripts/check_environment.py` | Ollama model + Earth Search collection reachable |
| `scripts/test_satellite_access.py` | Real STAC search; prints band assets and scale/offset |
| `scripts/run_investigation.py CASE_ID ["claim"] [--no-llm]` | Run one investigation in the terminal |
| `scripts/prepare_pair.py` | Render a before/after pair for any AOI |
| `scripts/scout_site.py` / `scripts/scan_region.py` | How demo sites were found from data |

## API

| Method | Path | |
|---|---|---|
| GET | `/health` | live Ollama + STAC checks |
| GET | `/demo-cases` | configured real cases |
| GET | `/indicators` | available indicators with example claims |
| POST | `/investigations` | `{claim, indicator: "auto"|id, case_id}` or `{claim, indicator, custom:{latitude, longitude, width_km, before_date, after_date}}` → 202 |
| GET | `/investigations`, `/investigations/{id}` | status, timeline, imagery, verdict, sources |
| GET | `/assets/{id}/{file}.png` | generated images (validated names only) |

Errors: `{"error": {"code", "message"}}` — e.g. `INVALID_LOCATION`, `UNSUPPORTED_CLAIM`,
`INSUFFICIENT_CLEAR_IMAGERY`, `SATELLITE_DOWNLOAD_FAILED`, `ANALYSIS_FAILED`, `INTERRUPTED`.

## Verified behaviour

- Cloudy scenes are rejected on **local** SCL coverage and other dates searched.
- No imagery (open-ocean AOI) → three real searches, then inconclusive; no analysis invented.
- Ollama offline → rule-based decisions, "visual interpretation unavailable", measurements and
  verdict still produced.
- Non-physical claim (taxes) → `UNSUPPORTED_CLAIM`.
- Server restart mid-run → `interrupted`, never `completed`.

## Limitations

- 10 m resolution; regions < 0.5 ha (forest) / 1 ha (mining) are not reported.
- Thresholds are configurable heuristics, not validated universal criteria.
- Indicators measure physical change; none proves cause, operator, completion, permits or legality.
- The built-up roof-vs-bare-soil split is a thin threshold (BSI ~0.09 roofs vs ~0.12 soil on the tested sites).
- The 4B VLM is fast but sometimes over-confident; its text never sets the verdict on its own.
- Demo-site names are descriptive; no company is identified as responsible for any change.

## Notes

- Earth Search assets carry per-asset `scale`/`offset`; calibration always reads them.
- Remote COG reads use the GDAL settings in `app/config.py` (`GDAL_COG_ENV`); ~46 s → ~2 s per window.
