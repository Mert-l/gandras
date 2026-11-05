import asyncio

from fastapi import APIRouter

from app.config import get_settings
from app.health import check_ollama, check_stac

router = APIRouter()


@router.get("/health")
async def health() -> dict:
    s = get_settings()
    ollama, stac = await asyncio.gather(check_ollama(s), check_stac(s))
    ok = ollama["model_available"] and stac["collection_available"]
    return {
        "status": "ok" if ok else "degraded",
        "model": s.vlm_model,
        "model_available": ollama["model_available"],
        "satellite_provider": "earth-search",
        "stac_collection": s.stac_collection,
        "checks": {"ollama": ollama, "stac": stac},
    }
