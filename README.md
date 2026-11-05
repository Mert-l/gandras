# The Earth Has Receipts

An agentic AI investigator that checks claims about the physical world — mine expansion
(**MineWatch**) and forest clearing (**ForestCheck**) — against real Sentinel-2 imagery.

The agent decides what to investigate, geospatial code measures, a local Qwen vision model
interprets, and a rule-based verification engine decides what the evidence supports. Reports
separate what was **observed** from what is **inferred** and what is **not established**.

See [docs/architecture.md](docs/architecture.md) and [docs/methodology.md](docs/methodology.md).

## Demo cases (all real imagery, results from the cached runs)

| Case | Claim | Verdict | Key evidence |
|---|---|---|---|
| `forest_sw_amazon_frontier` | No forest was cleared after 2020 | contradicted · high | 141 ha probable clearing in 23 regions (of 550 ha vegetation-index loss); 98% persists |
| `forest_amazonas_control` | Forest intact since 2020 | supported · medium | 0 ha loss over 2,500 ha |
| `mine_quebrada_blanca` | Stopped expanding after 2019 | contradicted · medium | 56.8 ha candidate disturbance; 86% persists |
| `mine_bingham_canyon` | Not expanded since 2019 | inconclusive · medium | 68.9 ha change but only 45% persists |

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

Runs all four demo cases once (~2 min first time). Afterwards they replay from cache in
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
| POST | `/investigations` | `{mode, claim, case_id}` or `{mode, claim, custom:{latitude, longitude, width_km, before_date, after_date}}` → 202 |
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
- MineWatch measures surface disturbance; it cannot prove expansion, cause, operator or legality.
- The 4B VLM is fast but sometimes over-confident; its text never sets the verdict on its own.
- Demo-site names are descriptive; no company is identified as responsible for any change.

## Notes

- Earth Search assets carry per-asset `scale`/`offset`; calibration always reads them.
- Remote COG reads use the GDAL settings in `app/config.py` (`GDAL_COG_ENV`); ~46 s → ~2 s per window.
