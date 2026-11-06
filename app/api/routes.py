import asyncio
import re

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse

from app import database
from app.agent.state import ASSETS_ROOT, load_demo_cases
from app.config import get_settings
from app.health import check_ollama, check_stac
from app.schemas import CreateInvestigation, ConfirmLocation
from app.services import investigations as svc

router = APIRouter()

INV_ID = re.compile(r"^inv_[0-9a-f]{10}$")
ASSET_NAME = re.compile(r"^[a-z0-9_]{1,40}\.png$")


def err(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


@router.get("/health")
async def health() -> dict:
    from fastapi.concurrency import run_in_threadpool

    from app.llm import registry
    s = get_settings()
    ollama, stac = await asyncio.gather(check_ollama(s), check_stac(s))
    try:
        default = await run_in_threadpool(registry.get_profile, None)
        model_label, model_ok = default.label, default.available
    except KeyError:
        model_label, model_ok = s.vlm_model, False
    ok = model_ok and stac["collection_available"]
    return {
        "status": "ok" if ok else "degraded",
        "model": model_label,
        "default_model": registry.default_model_id(),
        "model_available": model_ok,
        "satellite_provider": "earth-search",
        "stac_collection": s.stac_collection,
        "checks": {"ollama": ollama, "stac": stac},
        "location_discovery": {
            "search_provider": s.web_search_provider,
            "search_configured": bool(s.tavily_api_key) if s.web_search_provider == "tavily" else
                                 bool(s.brave_search_api_key) if s.web_search_provider == "brave" else
                                 bool(s.searxng_url) if s.web_search_provider == "searxng" else False,
            "geocoder_configured": bool(s.nominatim_url or s.photon_url),
        },
    }


@router.get("/models")
def models(refresh: bool = False) -> dict:
    """Models the server is configured to use. Never includes base URLs or API keys."""
    from app.llm import registry
    return {"default": registry.default_model_id(), "models": [p.public() for p in registry.list_profiles(refresh)]}


@router.get("/indicators")
def indicators() -> list[dict]:
    from app.indicators import public_list
    return public_list()


@router.get("/demo-cases")
def demo_cases() -> list[dict]:
    return load_demo_cases()


@router.post("/investigations", status_code=202)
def create_investigation(req: CreateInvestigation):
    try:
        st = svc.create(req.indicator, req.claim.strip(), req.case_id,
                        req.custom.model_dump() if req.custom else None, req.options.model_dump())
    except svc.RequestError as e:
        return err(400, e.code, str(e))
    return {"investigation_id": st.investigation_id, "status": st.status}


@router.get("/investigations")
def list_investigations() -> list[dict]:
    return database.list_recent()


@router.post("/investigations/{inv_id}/location", status_code=202)
def confirm_location(inv_id: str, req: ConfirmLocation):
    if not INV_ID.fullmatch(inv_id):
        return err(400, "INVALID_ID", "Malformed investigation id")
    try:
        st = svc.confirm_location(inv_id, req.candidate_id, req.custom.model_dump() if req.custom else None)
    except svc.RequestError as e:
        return err(404 if e.code == "NOT_FOUND" else 409 if e.code == "INVESTIGATION_BUSY" else 400, e.code, str(e))
    return {"investigation_id": st.investigation_id, "status": st.status}


@router.get("/investigations/{inv_id}")
def get_investigation(inv_id: str):
    if not INV_ID.match(inv_id):
        return err(400, "INVALID_ID", "Malformed investigation id")
    st = database.load(inv_id)
    if st is None:
        return err(404, "NOT_FOUND", "No such investigation")
    st.pop("candidates", None)
    return st


@router.get("/assets/{inv_id}/{name}")
def asset(inv_id: str, name: str):
    if not INV_ID.match(inv_id) or not ASSET_NAME.match(name):
        return err(400, "INVALID_ASSET", "Invalid asset reference")
    p = (ASSETS_ROOT / inv_id / name).resolve()
    if ASSETS_ROOT.resolve() not in p.parents or not p.is_file():
        return err(404, "NOT_FOUND", "No such asset")
    return FileResponse(p, media_type="image/png")


@router.get("/investigations/{inv_id}/report")
def evidence_report(inv_id: str, download: bool = False):
    from app.verification.report import render_evidence_report

    if not INV_ID.fullmatch(inv_id):
        return err(400, "INVALID_ID", "Malformed investigation id")
    st = database.load(inv_id)
    if st is None:
        return err(404, "NOT_FOUND", "No such investigation")
    headers = {"Cache-Control": "no-store"}
    if download:
        headers["Content-Disposition"] = f'attachment; filename="{inv_id}-evidence.html"'
    return HTMLResponse(render_evidence_report(st, ASSETS_ROOT), headers=headers)
