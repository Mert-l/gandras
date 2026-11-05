"""Scout a candidate demo site for any indicator with real imagery (no agent, no VLM).

Usage: python scripts/scout_indicator.py INDICATOR NAME LAT LON WIDTH_KM BEFORE AFTER [DIRECTION]
Writes before/after/overlay PNGs to data/investigations/scout/NAME/ and prints the result.
"""
import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

from app.analysis import visualize as viz  # noqa: E402
from app.config import PROJECT_ROOT, get_settings  # noqa: E402
from app.indicators import get  # noqa: E402
from app.satellite.aoi import AOI  # noqa: E402
from app.satellite.preprocessing import common_valid, render_rgb, shared_stretch, valid_mask  # noqa: E402
from app.satellite.raster_loader import load_scene  # noqa: E402
from app.satellite.scene_selector import describe_pair, rank_scenes  # noqa: E402
from app.satellite.stac_client import search_scenes  # noqa: E402


def best(aoi, grid, target, s):
    sc = search_scenes(aoi.bbox_wgs84(), (target - timedelta(days=45)).isoformat(),
                       min(target + timedelta(days=45), date.today()).isoformat(), max_cloud=60)
    u = [r for r in rank_scenes(sc, grid, target, s.max_local_invalid_percent, max_inspect=6) if r.usable]
    print(f"  {target}: {len(sc)} hits, best={u[0].scene.scene_id if u else None} inv={u[0].quality['invalid_pct'] if u else None}")
    return u[0].scene if u else None


ind_id, name, lat, lon, wkm, b, a = sys.argv[1:8]
ind = get(ind_id)
direction = sys.argv[8] if len(sys.argv) > 8 else ind.default_direction
s = get_settings()
aoi = AOI(name=name, lat=float(lat), lon=float(lon), width_m=float(wkm) * 1000)
grid = aoi.grid(s.analysis_resolution_m)
sb, sa = best(aoi, grid, date.fromisoformat(b), s), best(aoi, grid, date.fromisoformat(a), s)
if not (sb and sa):
    sys.exit("no usable pair")
B, A = load_scene(sb, grid, bands=ind.bands), load_scene(sa, grid, bands=ind.bands)
valid = common_valid(valid_mask(B["SCL"]), valid_mask(A["SCL"]))
res = ind.analyze(B, A, valid, grid, direction)
r = res["result"]
st = shared_stretch([B, A], [valid, valid])
rb, ra = render_rgb(B, st, valid_mask(B["SCL"]), 1.4), render_rgb(A, st, valid_mask(A["SCL"]), 1.4)
out = PROJECT_ROOT / "data" / "investigations" / "scout" / name
viz._save(rb, out / "before.png")
viz._save(ra, out / "after.png")
viz.overlay(ra, res["masks"]["evidence"], r["regions"], out / "overlay.png", other=res["masks"]["other"])
ov = np.asarray(__import__("PIL.Image", fromlist=["Image"]).open(out / "overlay.png"))
viz.side_by_side(rb, ov, out / "pair.png")
summary = {k: v for k, v in r.items() if not k.endswith("regions") and k != "regions"}
summary["top_regions"] = [{k: x.get(k) for k in ("id", "area_ha", "centroid_lonlat", "pattern", "kind", "spectral_pattern") if k in x}
                          for x in r["regions"][:6]]
summary["pair"] = describe_pair(sb, sa)
summary["scenes"] = [sb.scene_id, sa.scene_id]
print(json.dumps(summary, indent=1, default=str))
print(out / "pair.png")
