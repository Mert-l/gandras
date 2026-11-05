"""Wide scan (20 m grid) of a larger area to locate real forest-like loss for demo-site selection.

Usage: python scripts/scan_region.py NAME LAT LON WIDTH_KM BEFORE AFTER
Prints the largest forest-like-loss regions with coordinates.
"""
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.analysis.vegetation import VegetationThresholds, analyze_vegetation_change  # noqa: E402
from app.satellite.aoi import AOI  # noqa: E402
from app.satellite.preprocessing import common_valid, valid_mask  # noqa: E402
from app.satellite.raster_loader import load_scene  # noqa: E402
from app.satellite.scene_selector import rank_scenes  # noqa: E402
from app.satellite.stac_client import search_scenes  # noqa: E402

name, lat, lon, wkm, b, a = sys.argv[1:7]
aoi = AOI(name=name, lat=float(lat), lon=float(lon), width_m=float(wkm) * 1000)
grid = aoi.grid(20)
pick = {}
for k, d in (("b", b), ("a", a)):
    t = date.fromisoformat(d)
    sc = search_scenes(aoi.bbox_wgs84(), (t - timedelta(days=40)).isoformat(), (t + timedelta(days=40)).isoformat(), max_cloud=40)
    r = [x for x in rank_scenes(sc, grid, t, 10, max_inspect=6) if x.usable]
    if not r:
        sys.exit(f"{name}: no usable scene for {d}")
    pick[k] = r[0].scene
B, A = load_scene(pick["b"], grid), load_scene(pick["a"], grid)
res = analyze_vegetation_change(B, A, common_valid(valid_mask(B["SCL"]), valid_mask(A["SCL"])), grid,
                                VegetationThresholds(min_region_ha=2))["result"]
print(f"{name} {pick['b'].acquired_at.date()}->{pick['a'].acquired_at.date()} valid={res['valid_area_ha']} "
      f"forest_before={res['forest_like_before_ha']} forest_loss={res['forest_like_loss_ha']} probable={res['probable_clearing_ha']}")
for r in [r for r in res["regions"] if r["forest_like_before"]][:5]:
    print(f"   region {r['area_ha']} ha at lat={r['centroid_lonlat'][1]} lon={r['centroid_lonlat'][0]} ind={r['indicators']}")
