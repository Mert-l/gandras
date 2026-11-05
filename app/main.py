from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import logging

from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app import database
from app.api.routes import router
from app.config import get_settings

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    s = get_settings()
    s.resolve(s.cache_dir).mkdir(parents=True, exist_ok=True)
    s.resolve(s.database_path).parent.mkdir(parents=True, exist_ok=True)
    database.init_db()
    n = database.mark_interrupted()
    if n:
        logging.getLogger(__name__).warning("Marked %d unfinished investigation(s) as interrupted", n)
    yield


app = FastAPI(title="The Earth Has Receipts", version="0.1.0", lifespan=lifespan)
app.include_router(router)


@app.exception_handler(RequestValidationError)
async def _validation(_req, exc: RequestValidationError):
    msgs = "; ".join(f"{'.'.join(str(x) for x in e['loc'][1:])}: {e['msg']}" for e in exc.errors())
    code = "INVALID_LOCATION" if any("custom" in map(str, e["loc"]) for e in exc.errors()) else "INVALID_REQUEST"
    return JSONResponse(status_code=422, content={"error": {"code": code, "message": msgs},
                                                  "detail": [{"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()]})

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")
