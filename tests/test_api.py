from fastapi.testclient import TestClient

from app import health
from app.main import app


def _patch(monkeypatch, ollama: dict, stac: dict) -> None:
    async def fake_ollama(_s):
        return ollama

    async def fake_stac(_s):
        return stac

    monkeypatch.setattr("app.api.routes.check_ollama", fake_ollama)
    monkeypatch.setattr("app.api.routes.check_stac", fake_stac)


def test_health_ok(monkeypatch):
    _patch(monkeypatch, {"reachable": True, "model_available": True},
           {"reachable": True, "collection_available": True})
    r = TestClient(app).get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["model_available"] is True
    assert body["satellite_provider"] == "earth-search"


def test_health_reports_model_missing(monkeypatch):
    _patch(monkeypatch, {"reachable": False, "model_available": False},
           {"reachable": True, "collection_available": True})
    body = TestClient(app).get("/health").json()
    assert body["status"] == "degraded" and body["model_available"] is False


def test_check_ollama_unreachable_does_not_raise():
    import asyncio
    from app.config import Settings
    res = asyncio.run(health.check_ollama(Settings(ollama_url="http://127.0.0.1:9")))
    assert res["reachable"] is False and res["model_available"] is False


def test_index_served():
    r = TestClient(app).get("/")
    assert r.status_code == 200 and "THE EARTH HAS RECEIPTS" in r.text
