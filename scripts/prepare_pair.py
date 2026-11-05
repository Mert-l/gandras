"""Find, select and render a real before/after Sentinel-2 pair for one AOI.

Usage:
  python scripts/prepare_pair.py --name bingham --lat 40.523 --lon -112.150 --width-km 5 \
      --before 2019-07-15 --after 2024-07-15 [--window-days 45]
Writes PNGs + metadata.json to data/investigations/pairs/<name>/.
"""
import argparse
import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

from app.config import PROJECT_ROOT, get_settings  # noqa: E402
from app.satellite.aoi import AOI  # noqa: E402
from app.satellite.preprocessing import (common_valid, render_rgb, save_png, shared_stretch,  # noqa: E402
                                         valid_mask)
from app.satellite.raster_loader import load_scene  # noqa: E402
from app.satellite.scene_selector import describe_pair, rank_scenes  # noqa: E402
from app.satellite.stac_client import search_scenes  # noqa: E402


def pick(aoi, grid, target: date, window: int, max_invalid: float):
    scenes = search_scenes(aoi.bbox_wgs84(), (target - timedelta(days=window)).isoformat(),
                           (target + timedelta(days=window)).isoformat())
    print(f"  {len(scenes)} catalogue hits around {target}")
    ranked = rank_scenes(scenes, grid, target, max_invalid)
    for r in ranked:
        print(f"    {'OK ' if r.usable else 'BAD'} {r.scene.scene_id} {r.scene.acquired_at.date()} "
              f"scene_cloud={r.scene.cloud_cover:.1f}% {r.reason} no_data={r.quality['no_data_pct']}%")
    best = next((r for r in ranked if r.usable), None)
    if best is None:
        raise SystemExit(f"No usable scene within ±{window} days of {target}")
    return best


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True)
    ap.add_argument("--lat", type=float, required=True)
    ap.add_argument("--lon", type=float, required=True)
    ap.add_argument("--width-km", type=float, default=5)
    ap.add_argument("--before", required=True)
    ap.add_argument("--after", required=True)
    ap.add_argument("--window-days", type=int, default=45)
    a = ap.parse_args()

    s = get_settings()
    aoi = AOI(name=a.name, lat=a.lat, lon=a.lon, width_m=a.width_km * 1000)
    grid = aoi.grid(s.analysis_resolution_m)
    print(f"AOI {aoi.name}: EPSG:{grid.epsg} {grid.width}x{grid.height} px @ {grid.resolution_m} m")

    t0 = time.time()
    picks = {}
    for label, d in (("before", a.before), ("after", a.after)):
        print(f"[{label}]")
        picks[label] = pick(aoi, grid, date.fromisoformat(d), a.window_days, s.max_local_invalid_percent)

    data = {k: load_scene(r.scene, grid) for k, r in picks.items()}
    masks = {k: valid_mask(v["SCL"]) for k, v in data.items()}
    both = common_valid(masks["before"], masks["after"])
    stretch = shared_stretch([data["before"], data["after"]], [both, both])

    out = PROJECT_ROOT / "data" / "investigations" / "pairs" / a.name
    imgs = {k: render_rgb(data[k], stretch, mask=masks[k], gamma=1.4) for k in data}
    for k, img in imgs.items():
        save_png(img, out / f"{k}.png")
    gap = np.full((grid.height, 8, 3), 255, np.uint8)
    Image.fromarray(np.hstack([imgs["before"], gap, imgs["after"]])).save(out / "side_by_side.png")

    meta = {
        "aoi": aoi.model_dump(), "grid": grid.model_dump(), "pixel_area_m2": grid.pixel_area_m2,
        "common_valid_pct": round(100 * float(both.mean()), 2),
        "common_valid_area_ha": round(float(both.sum()) * grid.pixel_area_m2 / 10_000, 2),
        "pair": describe_pair(picks["before"].scene, picks["after"].scene),
        "stretch": stretch,
        "scenes": {k: {"scene_id": r.scene.scene_id, "acquired_at": r.scene.acquired_at.isoformat(),
                       "collection": r.scene.collection, "stac_item": r.scene.self_href,
                       "processing_baseline": r.scene.processing_baseline,
                       "calibration": {b: {"scale": x.scale, "offset": x.offset} for b, x in r.scene.assets.items()},
                       "local_quality": {kk: vv for kk, vv in r.quality.items() if kk != "scl_counts"}}
                   for k, r in picks.items()},
        "reflectance_check": {k: {b: round(float(np.nanmedian(v[b][masks[k]])), 4) for b in ("B02", "B03", "B04", "B08", "B11")}
                              for k, v in data.items()},
    }
    (out / "metadata.json").write_text(json.dumps(meta, indent=2, default=str))
    print(json.dumps({k: meta[k] for k in ("common_valid_pct", "common_valid_area_ha", "pair", "reflectance_check")}, indent=1))
    print(f"Wrote {out} in {time.time() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
