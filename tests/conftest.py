import pytest

from app.config import get_settings


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    """Every test gets its own SQLite file so tests never touch real investigations."""
    monkeypatch.setattr(get_settings(), "database_path", tmp_path / "test.db")
    from app import database
    database.init_db()
    yield


@pytest.fixture(autouse=True)
def _fixed_models(monkeypatch):
    """Tests never depend on what this machine's Ollama has installed."""
    from app.llm import registry

    def fake_ollama(base_url, location, prefix="ollama"):
        return [registry.ModelProfile(id=f"{prefix}:qwen3.5:4b", label="Qwen 3.5 4B", provider="ollama",
                                      model="qwen3.5:4b", base_url=base_url, location=location, vision=True,
                                      available=True, status="ready"),
                registry.ModelProfile(id=f"{prefix}:gemma4:latest", label="Gemma4", provider="ollama",
                                      model="gemma4:latest", base_url=base_url, location=location, vision=False,
                                      available=True, status="ready")]

    monkeypatch.setattr(registry, "_ollama_profiles", fake_ollama)
    monkeypatch.setattr(get_settings(), "models_file", "tests/__no_models_file__.json")
    monkeypatch.setattr(get_settings(), "default_model", "")
    registry._cache.update(at=0.0, profiles=None)
    yield
    registry._cache.update(at=0.0, profiles=None)
