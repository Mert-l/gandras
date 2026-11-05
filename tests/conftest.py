import pytest

from app.config import get_settings


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    """Every test gets its own SQLite file so tests never touch real investigations."""
    monkeypatch.setattr(get_settings(), "database_path", tmp_path / "test.db")
    from app import database
    database.init_db()
    yield
