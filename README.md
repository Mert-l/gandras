# The Earth Has Receipts

Agentic AI that verifies real-world claims (MineWatch, ForestCheck) against real Sentinel-2 imagery.

## Status

- **Phase 1 done:** FastAPI backend with a live `/health` check (Ollama + Earth Search), environment checks.
- **Phase 2 done:** real Sentinel-2 retrieval — STAC search, per-AOI SCL quality ranking, calibrated bands reprojected onto one common 10 m UTM grid, consistent RGB previews, caching.

Not built yet: change detection, VLM, agent, full UI.

## Setup

Requires Python 3.12 and [Ollama](https://ollama.com).

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
ollama pull qwen3.5:4b
```

## Check the environment

```bash
python scripts/check_environment.py        # Ollama model + Earth Search collection
python scripts/test_satellite_access.py    # real STAC search, prints band assets + scale/offset
```

## Prepare a real before/after pair

```bash
python scripts/prepare_pair.py --name bingham --lat 40.523 --lon -112.150 --width-km 5 \
    --before 2019-07-15 --after 2024-07-15
```

Searches ±45 days around each date, ranks scenes by **local** SCL quality over the AOI,
and writes `before.png`, `after.png`, `side_by_side.png` and `metadata.json`
(scene IDs, acquisition times, calibration, quality) to `data/investigations/pairs/<name>/`.
First run ~90 s (network); reruns hit the cache.

## Run

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

- App: http://localhost:8000
- API docs: http://localhost:8000/docs
- Health: http://localhost:8000/health

## Test

```bash
pytest -q
```

## Notes

- Scene-wide `eo:cloud_cover` is only a pre-filter. Example: `S2B_T12TUK_20190715T182114_L2A` reports 3.1 % cloud scene-wide but 71 % invalid over the Bingham AOI, so it is rejected.
- Pixels with SCL classes no-data, saturated, cloud shadow, cloud (med/high), cirrus, snow are invalid; they render purple in previews and are excluded from measurements.
- Previews use one shared 2–98 % stretch computed over pixels valid on both dates, so unchanged ground renders identically.

- Earth Search `sentinel-2-c1-l2a` assets carry per-asset `scale=0.0001`, `offset=-0.1` (baseline ≥ 04.00). Calibration must read these from STAC metadata, never assume.
- Remote COG reads use the GDAL settings in `app/config.py` (`GDAL_COG_ENV`); without them a 5 km window took ~46 s instead of ~2 s.
