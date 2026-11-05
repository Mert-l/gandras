"""Verify Python deps, Ollama model, and Earth Search access. Exit 1 on failure."""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.health import check_ollama, check_stac  # noqa: E402


async def main() -> int:
    s = get_settings()
    import cv2, rasterio, pystac_client  # noqa: F401,E401
    print(f"rasterio {rasterio.__version__} / GDAL {rasterio.__gdal_version__}")
    ollama, stac = await asyncio.gather(check_ollama(s), check_stac(s))
    print("ollama:", json.dumps(ollama))
    print("stac:  ", json.dumps(stac))
    ok = ollama["model_available"] and stac["collection_available"]
    print("ENVIRONMENT OK" if ok else "ENVIRONMENT NOT READY")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
