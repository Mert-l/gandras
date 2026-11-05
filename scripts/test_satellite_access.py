"""Run a real STAC search and print item metadata, band assets and calibration info.

Usage: python scripts/test_satellite_access.py [lon lat] [start/end]
Default point: Bingham Canyon Mine, Utah (used only as a connectivity probe).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pystac_client import Client  # noqa: E402

from app.config import get_settings  # noqa: E402

BANDS = {"blue": "B02", "green": "B03", "red": "B04", "nir": "B08", "swir16": "B11", "scl": "SCL"}


def main() -> int:
    s = get_settings()
    lon, lat = (float(sys.argv[1]), float(sys.argv[2])) if len(sys.argv) >= 3 else (-112.150, 40.523)
    dt = sys.argv[3] if len(sys.argv) >= 4 else "2024-06-01/2024-08-31"
    d = 0.02
    catalog = Client.open(s.stac_api_url)
    items = list(catalog.search(
        collections=[s.stac_collection],
        bbox=[lon - d, lat - d, lon + d, lat + d],
        datetime=dt,
        query={"eo:cloud_cover": {"lt": s.max_scene_cloud_percent}},
        max_items=10,
    ).items())
    print(f"{len(items)} items from {s.stac_collection} near ({lon}, {lat}) in {dt}")
    if not items:
        return 1
    for it in items[:5]:
        print(f"  {it.id}  {it.datetime.isoformat()}  cloud={it.properties.get('eo:cloud_cover'):.1f}%")
    it = items[0]
    print(f"\nAssets of {it.id}:")
    for name, band in BANDS.items():
        a = it.assets.get(name)
        if a is None:
            print(f"  {band:4s} ({name}): MISSING")
            continue
        rb = (a.extra_fields.get("raster:bands") or [{}])[0]
        print(f"  {band:4s} ({name}): scale={rb.get('scale')} offset={rb.get('offset')} "
              f"res={rb.get('spatial_resolution')} nodata={rb.get('nodata')}\n        {a.href}")
    print(f"\nprocessing baseline: {it.properties.get('s2:processing_baseline')}  epsg: {it.properties.get('proj:epsg') or it.properties.get('proj:code')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
