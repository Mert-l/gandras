import asyncio
import re

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse

from app import database
from app.agent.state import ASSETS_ROOT, load_demo_cases
from app.config import get_settings
from app.health import check_ollama, check_stac
from app.schemas import CreateInvestigation
from app.services import investigations as svc

router = APIRouter()

INV_ID = re.compile(r"^inv_[0-9a-f]{10}$")
ASSET_NAME = re.compile(r"^[a-z0-9_]{1,40}\.png$")


def err(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


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


@router.get("/demo-cases")
def demo_cases() -> list[dict]:
    return load_demo_cases()


@router.post("/investigations", status_code=202)
def create_investigation(req: CreateInvestigation):
    try:
        st = svc.create(req.mode, req.claim.strip(), req.case_id,
                        req.custom.model_dump() if req.custom else None, req.options.model_dump())
    except svc.RequestError as e:
        return err(400, e.code, str(e))
    return {"investigation_id": st.investigation_id, "status": st.status}


@router.get("/investigations")
def list_investigations() -> list[dict]:
    return database.list_recent()


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
