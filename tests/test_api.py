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


def test_demo_cases_listed():
    r = TestClient(app).get("/demo-cases")
    assert r.status_code == 200
    ids = {c["id"] for c in r.json()}
    assert {"mine_quebrada_blanca", "forest_sw_amazon_frontier"} <= ids


def test_create_rejects_bad_requests():
    c = TestClient(app)
    assert c.post("/investigations", json={"mode": "volcano", "claim": "hello there", "case_id": "x"}).status_code == 422
    r = c.post("/investigations", json={"mode": "forest", "claim": "No clearing after 2020",
                                        "custom": {"latitude": 95, "longitude": 0, "width_km": 5,
                                                   "before_date": "2020-01-01", "after_date": "2021-01-01"}})
    assert r.status_code == 422 and r.json()["error"]["code"] == "INVALID_LOCATION"
    r = c.post("/investigations", json={"mode": "forest", "claim": "No clearing after 2020",
                                        "custom": {"latitude": 0, "longitude": 0, "width_km": 50,
                                                   "before_date": "2020-01-01", "after_date": "2021-01-01"}})
    assert r.status_code == 422
    assert c.post("/investigations", json={"mode": "forest", "claim": "x" * 501, "case_id": "forest_amazonas_control"}).status_code == 422
    r = c.post("/investigations", json={"mode": "mining", "claim": "No clearing after 2020", "case_id": "forest_amazonas_control"})
    assert r.status_code == 400


def test_create_returns_202_and_runs(monkeypatch):
    from app.services import investigations as svc
    submitted = []
    monkeypatch.setattr(svc._executor, "submit", lambda fn, inv: submitted.append(inv))
    c = TestClient(app)
    r = c.post("/investigations", json={"mode": "forest", "claim": "No forest was cleared after 2020",
                                        "case_id": "forest_amazonas_control"})
    assert r.status_code == 202 and r.json()["status"] == "queued"
    inv = r.json()["investigation_id"]
    assert submitted == [inv]
    g = c.get(f"/investigations/{inv}").json()
    assert g["status"] == "queued" and g["location"]["latitude"] == -8.25
    assert any(x["investigation_id"] == inv for x in c.get("/investigations").json())


def test_unknown_and_malformed_ids():
    c = TestClient(app)
    assert c.get("/investigations/inv_0000000000").status_code == 404
    assert c.get("/investigations/../../etc").status_code in (400, 404)
    assert c.get("/investigations/DROP TABLE").status_code == 400


def test_asset_path_traversal_rejected():
    c = TestClient(app)
    assert c.get("/assets/inv_0000000000/..%2F..%2Fapp%2Fconfig.py").status_code in (400, 404)
    assert c.get("/assets/inv_0000000000/config.py").status_code == 400
    assert c.get("/assets/..%2Fx/before.png").status_code in (400, 404)
    assert c.get("/assets/inv_0000000000/before.png").status_code == 404


def test_restart_marks_running_as_interrupted():
    from app import database
    database.save({"investigation_id": "inv_aaaaaaaaaa", "status": "running", "mode": "forest", "claim": "c",
                   "case_id": None, "created_at": database.now(), "steps": [{"status": "running"}]})
    assert database.mark_interrupted() == 1
    s = database.load("inv_aaaaaaaaaa")
    assert s["status"] == "interrupted" and s["error"]["code"] == "INTERRUPTED" and s["steps"][0]["status"] == "failed"
