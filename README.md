# The Earth Has Receipts

Agentic AI that verifies real-world claims (MineWatch, ForestCheck) against real Sentinel-2 imagery.

## Status

**Phase 1 done:** FastAPI backend with a live `/health` check (Ollama + Earth Search), environment and satellite-access scripts, tests.
Not built yet: imagery retrieval, change detection, VLM, agent, full UI.

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

- Earth Search `sentinel-2-c1-l2a` assets carry per-asset `scale=0.0001`, `offset=-0.1` (baseline ≥ 04.00). Calibration must read these from STAC metadata, never assume.
- Remote COG reads use the GDAL settings in `app/config.py` (`GDAL_COG_ENV`); without them a 5 km window took ~46 s instead of ~2 s.
