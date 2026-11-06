"""Model registry, provider backends and per-investigation model selection."""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.llm import backends, registry
from app.main import app
from app.vlm import client as vc
from app.vlm.schemas import VisualAnalysis


def _write_models(tmp_path, monkeypatch, entries):
    path = tmp_path / "models.json"
    path.write_text(json.dumps(entries))
    monkeypatch.setattr(get_settings(), "models_file", str(path))
    registry._cache.update(at=0.0, profiles=None)


def test_models_endpoint_lists_discovered_and_configured(tmp_path, monkeypatch):
    monkeypatch.delenv("TEST_CLOUD_KEY", raising=False)
    _write_models(tmp_path, monkeypatch, [
        {"_comment": "ignored"},
        {"provider": "anthropic", "id": "anthropic:test", "model": "claude-x", "api_key_env": "TEST_CLOUD_KEY", "vision": True},
        {"provider": "bogus", "id": "x", "model": "y"},
    ])
    body = TestClient(app).get("/models").json()
    by_id = {m["id"]: m for m in body["models"]}
    assert body["default"] == "ollama:qwen3.5:4b"
    assert by_id["ollama:qwen3.5:4b"]["vision"] is True and by_id["ollama:gemma4:latest"]["vision"] is False
    assert by_id["anthropic:test"]["available"] is False and "API key missing" in by_id["anthropic:test"]["status"]
    assert "x" not in by_id                                         # invalid provider entry ignored
    assert all("base_url" not in m and "api_key_env" not in m for m in body["models"])  # no endpoints/keys leak


def test_unknown_or_unavailable_model_rejected(tmp_path, monkeypatch):
    monkeypatch.delenv("TEST_CLOUD_KEY", raising=False)
    _write_models(tmp_path, monkeypatch, [
        {"provider": "anthropic", "id": "anthropic:test", "model": "claude-x", "api_key_env": "TEST_CLOUD_KEY"}])
    c = TestClient(app)
    base = {"claim": "No forest was cleared after 2020.", "case_id": "forest_amazonas_control"}
    r = c.post("/investigations", json={**base, "options": {"model": "ollama:nope"}})
    assert r.status_code == 400 and r.json()["error"]["code"] == "UNKNOWN_MODEL"
    r = c.post("/investigations", json={**base, "options": {"model": "anthropic:test"}})
    assert r.status_code == 400 and r.json()["error"]["code"] == "MODEL_UNAVAILABLE"
    assert c.post("/investigations", json={**base, "options": {"model": "http://evil/x?y"}}).status_code == 422


def test_selected_model_is_recorded(monkeypatch):
    from app.services import investigations as svc
    monkeypatch.setattr(svc._executor, "submit", lambda *a: None)
    c = TestClient(app)
    r = c.post("/investigations", json={"claim": "No forest was cleared after 2020.", "case_id": "forest_amazonas_control",
                                        "options": {"model": "ollama:gemma4:latest"}})
    inv = c.get(f"/investigations/{r.json()['investigation_id']}").json()
    assert inv["model"]["id"] == "ollama:gemma4:latest" and inv["model"]["vision"] is False
    assert c.get("/investigations").json()[0]["model_label"] == "Gemma4"


def test_active_model_routes_chat(monkeypatch):
    """structured_chat uses the model of the surrounding investigation, not a global setting."""
    used = []

    class FakeResp:
        class message:
            content = '{"action": "finalize"}'
        done_reason = "stop"

    class FakeClient:
        def chat(self, model, **kw):
            used.append(model)
            return FakeResp()

    monkeypatch.setattr(vc, "_client", lambda timeout_s: FakeClient())
    from pydantic import BaseModel

    class Choice(BaseModel):
        action: str

    vc.structured_chat("s", "u", Choice)
    with registry.use(registry.get_profile("ollama:gemma4:latest")):
        vc.structured_chat("s", "u", Choice)
    assert used == ["qwen3.5:4b", "gemma4:latest"]


def test_text_only_model_skips_images_honestly(tmp_path):
    from PIL import Image
    paths = []
    for n in ("b", "a", "o"):
        p = tmp_path / f"{n}.png"
        Image.new("RGB", (8, 8)).save(p)
        paths.append(p)
    with registry.use(registry.get_profile("ollama:gemma4:latest")):
        r = vc.interpret_images(*paths, mode="forest", claim="c", before_date="2020-01-01", after_date="2021-01-01",
                                measurements={})
    assert r.status == "unavailable" and "cannot read images" in r.error and r.model == "Gemma4"


def _schema():
    return VisualAnalysis.model_json_schema()


def test_openai_backend_json_schema_then_fallback(tmp_path):
    calls = []

    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        if body["response_format"]["type"] == "json_schema":
            return httpx.Response(400, json={"error": {"message": "response_format json_schema not supported"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"ok": 1}'}, "finish_reason": "stop"}]})

    from PIL import Image
    img = tmp_path / "i.png"
    Image.new("RGB", (4, 4)).save(img)
    p = registry.ModelProfile(id="o", label="O", provider="openai", model="m", base_url="http://srv/v1", vision=True)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        text, trunc = backends.openai_chat(p, [{"role": "system", "content": "sys"}, {"role": "user", "content": "u"}],
                                           _schema(), "VisualAnalysis", [img], 0.1, 10, client=client)
    assert text == '{"ok": 1}' and trunc is False
    assert calls[0]["messages"][1]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert calls[1]["response_format"] == {"type": "json_object"} and "JSON schema" in calls[1]["messages"][0]["content"]


def test_anthropic_backend_forced_tool(monkeypatch, tmp_path):
    monkeypatch.setenv("TEST_CLOUD_KEY", "sk-test")
    seen = {}

    def handler(request):
        seen["headers"], seen["body"] = request.headers, json.loads(request.content)
        return httpx.Response(200, json={"content": [{"type": "tool_use", "name": "submit_result", "input": {"a": 1}}],
                                         "stop_reason": "tool_use"})

    p = registry.ModelProfile(id="c", label="C", provider="anthropic", model="claude-x", base_url="https://api.example",
                              api_key_env="TEST_CLOUD_KEY", vision=True)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        text, trunc = backends.anthropic_chat(p, [{"role": "system", "content": "sys"}, {"role": "user", "content": "u"}],
                                              _schema(), "VisualAnalysis", None, 0.1, 10, client=client)
    assert json.loads(text) == {"a": 1} and trunc is False
    assert seen["headers"]["x-api-key"] == "sk-test" and seen["body"]["system"] == "sys"
    assert seen["body"]["tool_choice"] == {"type": "tool", "name": "submit_result"}
    assert seen["body"]["messages"] == [{"role": "user", "content": "u"}]


def test_backend_error_does_not_leak_key(monkeypatch):
    monkeypatch.setenv("TEST_CLOUD_KEY", "sk-secret-123")
    p = registry.ModelProfile(id="c", label="C", provider="anthropic", model="m", base_url="https://api.example",
                              api_key_env="TEST_CLOUD_KEY")
    handler = lambda r: httpx.Response(401, json={"error": {"message": "invalid x-api-key"}})  # noqa: E731
    with httpx.Client(transport=httpx.MockTransport(handler)) as client, pytest.raises(backends.BackendError) as e:
        backends.anthropic_chat(p, [{"role": "user", "content": "u"}], {}, "S", None, 0.1, 10, client=client)
    assert "401" in str(e.value) and "sk-secret" not in str(e.value)
