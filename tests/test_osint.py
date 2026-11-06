"""Open-source context step and planning/vision model split."""
import pytest
from fastapi.testclient import TestClient

from app.agent import osint, orchestrator
from app.location import providers
from app.main import app

from tests.test_agent import _state, mocked  # noqa: F401  (fixture reuse)


def test_trigger_reasons():
    assert osint.trigger_reason({"status": "contradicted", "confidence": "high"}, {"x": 1}) is None
    assert "inconclusive" in osint.trigger_reason({"status": "inconclusive", "confidence": "medium"}, {"x": 1})
    assert "low confidence" in osint.trigger_reason({"status": "supported", "confidence": "low"}, {"x": 1})
    assert "no usable" in osint.trigger_reason({"status": "inconclusive", "confidence": "low"}, None)


@pytest.fixture
def web(monkeypatch):
    page = "Local news: crews expanded the north pit wall in 2022, the operator confirmed new benches were cut."
    monkeypatch.setattr(providers, "search_web", lambda q: [{"url": "https://news.example/a", "title": "Mine news"}])
    monkeypatch.setattr(providers, "fetch_source", lambda url: {"url": url, "text": page, "retrieved_at": "2026-11-06T00:00:00"})

    def chat(system, user, schema, **k):
        if schema is osint.OsintPlan:
            return osint.OsintPlan(queries=["mine expansion 2022"])
        return osint.OsintFindings(summary="Reporting describes expansion.", findings=[
            osint.OsintFinding(source_id=1, quote="crews expanded the north pit wall in 2022", stance="contradicts_claim"),
            osint.OsintFinding(source_id=1, quote="the mine was shut down permanently in 2020", stance="supports_claim")])
    import app.vlm.client as vc
    monkeypatch.setattr(vc, "structured_chat", chat)


def test_research_keeps_only_verbatim_quotes(web):
    out = osint.research("The pit stopped expanding after 2019.", "Test Mine", "2019 to 2024", "inconclusive")
    assert out["status"] == "findings" and len(out["findings"]) == 1
    assert out["findings"][0]["stance"] == "contradicts_claim" and out["findings"][0]["url"] == "https://news.example/a"
    assert any("not found verbatim" in e for e in out["errors"])          # the invented quote was dropped
    assert out["counts"] == {"supports_claim": 0, "contradicts_claim": 1, "context": 0}


def test_research_without_search_provider_is_reported(monkeypatch):
    def no_search(q):
        raise providers.ResearchError("Set TAVILY_API_KEY in .env")
    monkeypatch.setattr(providers, "search_web", no_search)
    import app.vlm.client as vc
    monkeypatch.setattr(vc, "structured_chat", lambda *a, **k: (_ for _ in ()).throw(vc.VLMError("x")))
    out = osint.research("claim text here", "Place", "2019 to 2024", "inconclusive")
    assert out["status"] == "unavailable" and "TAVILY" in out["summary"]


def test_runner_adds_context_without_changing_verdict(mocked, web, monkeypatch):  # noqa: F811
    calls, _, result = mocked
    result["clearing"], result["persist"] = 3.0, 0.9          # ambiguous -> inconclusive
    import app.vlm.client as vc
    plain_chat = vc.structured_chat

    def chat(system, user, schema, **k):                       # planner/claim calls fail -> rules; OSINT calls answered
        if schema in (osint.OsintPlan, osint.OsintFindings):
            return plain_chat(system, user, schema, **k)
        raise vc.VLMError("planner offline")
    monkeypatch.setattr(vc, "structured_chat", chat)
    st = orchestrator.Runner(_state(), use_llm=True).run()
    actions = [s.action for s in st.steps]
    assert "research_open_sources" in actions and actions.index("research_open_sources") < actions.index("finalize")
    assert st.verdict["status"] == "inconclusive"               # context never changes the satellite verdict
    assert st.osint["status"] == "findings"
    assert any("Open-source context" in x for x in st.verdict["inferred"])
    assert any("do not change the verdict" in x for x in st.limitations)


def test_strong_verdict_skips_open_sources(mocked, monkeypatch):  # noqa: F811
    import app.vlm.client as vc
    monkeypatch.setattr(vc, "structured_chat", lambda *a, **k: (_ for _ in ()).throw(vc.VLMError("x")))
    monkeypatch.setattr(providers, "search_web", lambda q: pytest.fail("should not search"))
    st = orchestrator.Runner(_state(), use_llm=True).run()     # 40 ha, persists -> contradicted, high
    assert st.osint is None and "research_open_sources" not in [s.action for s in st.steps]


def test_vision_model_split_and_validation(monkeypatch):
    from app.services import investigations as svc
    monkeypatch.setattr(svc._executor, "submit", lambda *a: None)
    c = TestClient(app)
    base = {"claim": "No forest was cleared after 2020.", "case_id": "forest_amazonas_control"}
    r = c.post("/investigations", json={**base, "options": {"model": "ollama:gemma4:latest", "vision_model": "ollama:qwen3.5:4b"}})
    inv = c.get(f"/investigations/{r.json()['investigation_id']}").json()
    assert inv["model"]["id"] == "ollama:gemma4:latest" and inv["vision_model"]["id"] == "ollama:qwen3.5:4b"
    r = c.post("/investigations", json={**base, "options": {"vision_model": "ollama:gemma4:latest"}})
    assert r.status_code == 400 and r.json()["error"]["code"] == "VISION_UNSUPPORTED"


def test_image_step_runs_on_vision_model(mocked, monkeypatch):  # noqa: F811
    monkeypatch.setattr(orchestrator.Runner, "_interpret_images",
                        lambda self: (setattr(self.st, "visual_analysis", {"status": "ok"}), ("ok", {}))[1])
    st = _state()
    st.model = {"id": "ollama:gemma4:latest", "vision": False}
    st.vision_model = {"id": "ollama:qwen3.5:4b", "vision": True}
    st = orchestrator.Runner(st, use_llm=False).run()
    assert "skip_visual_interpretation" not in [s.action for s in st.steps]   # vision model can see
    assert st.vision_model["id"] == "ollama:qwen3.5:4b" and st.model["id"] == "ollama:gemma4:latest"


def test_relevant_excerpt_finds_statements_deep_in_the_page():
    filler = "Unrelated navigation text about products and careers here. " * 120
    page = ("Bingham Canyon is a mine in Utah. " + filler +
            "In September 2022, we approved a $55 million investment to start underground mining and expand production at Kennecott. "
            + filler)
    ex = osint.relevant_excerpt(page, ["Bingham", "Canyon", "mine", "expan"], limit=600)
    assert "September 2022" in ex and "expand production" in ex
    assert len(ex) <= 600
