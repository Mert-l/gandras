"""Live dependency checks (never hardcoded)."""
import httpx

from app.config import Settings


async def check_ollama(s: Settings) -> dict:
    """Ask Ollama which models it has; report whether the configured one is present."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as c:
            r = await c.get(f"{s.ollama_url.rstrip('/')}/api/tags")
            r.raise_for_status()
        models = {m["name"]: m for m in r.json().get("models", [])}
        m = models.get(s.vlm_model)
        return {
            "reachable": True,
            "model_available": m is not None,
            "vision_capable": bool(m and "vision" in m.get("capabilities", [])),
        }
    except Exception as e:  # noqa: BLE001
        return {"reachable": False, "model_available": False, "error": type(e).__name__}


async def check_stac(s: Settings) -> dict:
    """Confirm the STAC API answers and the configured collection exists."""
    url = f"{s.stac_api_url.rstrip('/')}/collections/{s.stac_collection}"
    try:
        async with httpx.AsyncClient(timeout=s.http_timeout_s) as c:
            r = await c.get(url)
        return {"reachable": True, "collection_available": r.status_code == 200}
    except Exception as e:  # noqa: BLE001
        return {"reachable": False, "collection_available": False, "error": type(e).__name__}
