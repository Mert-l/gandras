"""Application configuration, loaded from environment / .env."""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=PROJECT_ROOT / ".env", extra="ignore")

    app_env: str = "development"

    stac_api_url: str = "https://earth-search.aws.element84.com/v1"
    stac_collection: str = "sentinel-2-c1-l2a"

    ollama_url: str = "http://localhost:11434"
    vlm_model: str = "qwen3.5:4b"
    # Default model id from the registry, e.g. "ollama:qwen3.5:4b" or an id from MODELS_FILE.
    default_model: str = ""
    models_file: str = "config/models.json"

    max_agent_steps: int = 8
    max_imagery_retries: int = 2

    max_aoi_km2: float = 100.0
    analysis_resolution_m: float = 10.0

    max_scene_cloud_percent: float = 35.0
    max_local_invalid_percent: float = 15.0

    enable_cache: bool = True
    cache_dir: Path = PROJECT_ROOT / "data" / "cache"
    database_path: Path = PROJECT_ROOT / "data" / "investigations.db"

    http_timeout_s: float = 20.0
    web_search_provider: str = "tavily"
    tavily_api_key: str = ""
    brave_search_api_key: str = ""
    searxng_url: str = ""
    # Set explicitly to a hosted/self-hosted Nominatim-compatible endpoint.
    nominatim_url: str = ""
    photon_url: str = "https://photon.komoot.io"
    location_user_agent: str = "Gandras/0.2 (https://github.com/Mert-l/gandras)"
    location_search_attempts: int = 3
    provisional_aoi_width_km: float = 5.0

    def resolve(self, p: Path) -> Path:
        return p if p.is_absolute() else (PROJECT_ROOT / p).resolve()


@lru_cache
def get_settings() -> Settings:
    return Settings()


# GDAL options for fast windowed reads of remote Cloud Optimized GeoTIFFs.
# Without these, a 5 km window read was measured at ~46 s; with them ~2 s.
GDAL_COG_ENV = {
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif,.TIF,.tiff",
    "GDAL_HTTP_MERGE_CONSECUTIVE_RANGES": "YES",
    "GDAL_HTTP_MULTIPLEX": "YES",
    "GDAL_HTTP_VERSION": "2",
    "VSI_CACHE": "TRUE",
    "GDAL_HTTP_MAX_RETRY": "3",
    "GDAL_HTTP_RETRY_DELAY": "1",
}
