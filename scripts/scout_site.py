"""Scout a candidate AOI with real data: pick best scenes, run an analysis, write previews.

Usage: python scripts/scout_site.py forest|mining NAME LAT LON WIDTH_KM BEFORE AFTER [WINDOW_DAYS]
"""
import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.analysis import visualize as viz  # noqa: E402
from app.config import PROJECT_ROOT, get_settings  # noqa: E402
from app.satellite.aoi import AOI  # noqa: E402
from app.satellite.preprocessing import common_valid, render_rgb, shared_stretch, valid_mask  # noqa: E402
from app.satellite.raster_loader import load_scene  # noqa: E402
from app.satellite.scene_selector import describe_pair, rank_scenes  # noqa: E402
from app.satellite.stac_client import search_scenes  # noqa: E402


def best(aoi, grid, target, window, max_inv):
    sc = search_scenes(aoi.bbox_wgs84(), (target - timedelta(days=window)).isoformat(),
                       (target + timedelta(days=window)).isoformat(), max_cloud=60)
    r = rank_scenes(sc, grid, target, max_inv)
    u = [x for x in r if x.usable]
    print(f"  {target}: {len(sc)} hits, {len(u)} usable; best={u[0].scene.scene_id if u else None} "
          f"inv={u[0].quality['invalid_pct'] if u else None}")
    return u[0] if u else None


def main():
    mode, name, lat, lon, wkm, b, a = sys.argv[1:8]
    window = int(sys.argv[8]) if len(sys.argv) > 8 else 45
    s = get_settings()
    aoi = AOI(name=name, lat=float(lat), lon=float(lon), width_m=float(wkm) * 1000)
    grid = aoi.grid(s.analysis_resolution_m)
    rb = best(aoi, grid, date.fromisoformat(b), window, s.max_local_invalid_percent)
    ra = best(aoi, grid, date.fromisoformat(a), window, s.max_local_invalid_percent)
    if not (rb and ra):
        sys.exit("no usable pair")
    B, A = load_scene(rb.scene, grid), load_scene(ra.scene, grid)
    valid = common_valid(valid_mask(B["SCL"]), valid_mask(A["SCL"]))
    if mode == "forest":
        from app.analysis.vegetation import analyze_vegetation_change
        res = analyze_vegetation_change(B, A, valid, grid)
    else:
        from app.analysis.mining import analyze_surface_disturbance
        res = analyze_surface_disturbance(B, A, valid, grid)
    st = shared_stretch([B, A], [valid, valid])
    rgbB, rgbA = render_rgb(B, st, valid_mask(B["SCL"]), 1.4), render_rgb(A, st, valid_mask(A["SCL"]), 1.4)
    out = PROJECT_ROOT / "data" / "investigations" / "scout" / name
    viz.side_by_side(rgbB, viz.overlay(rgbA, res["masks"]["change"], res["result"]["regions"], out / "ov.png") and
                     __import__("numpy").asarray(__import__("PIL.Image").Image.open(out / "ov.png")), out / "pair.png")
    r = {k: v for k, v in res["result"].items() if k != "regions"}
    r["top_regions"] = res["result"]["regions"][:5]
    r["pair"] = describe_pair(rb.scene, ra.scene)
    r["dates"] = [str(rb.scene.acquired_at.date()), str(ra.scene.acquired_at.date())]
    print(json.dumps(r, indent=1, default=str))
    print(out / "pair.png")


if __name__ == "__main__":
    main()
