"""Read Sentinel-2 bands onto the common AnalysisGrid with calibration applied.

- Reprojection uses WarpedVRT onto the grid's CRS/transform/shape (never image resizing).
- Reflectance bands: bilinear; SCL (categorical): nearest.
- Calibration: reflectance = DN * scale + offset, from each asset's STAC raster:bands metadata.
- Nodata (DN == asset nodata) becomes NaN, never zero reflectance.
"""
from __future__ import annotations

import time

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.vrt import WarpedVRT

from app.config import GDAL_COG_ENV
from app.satellite import cache
from app.satellite.aoi import AnalysisGrid
from app.satellite.stac_client import SceneMeta

REFLECTANCE_BANDS = ["B02", "B03", "B04", "B08", "B11"]
ALL_BANDS = REFLECTANCE_BANDS + ["SCL"]


class CalibrationError(ValueError):
    pass


def _read_on_grid(href: str, grid: AnalysisGrid, resampling: Resampling, src_nodata: float) -> np.ndarray:
    last: Exception | None = None
    for attempt in range(3):
        try:
            with rasterio.Env(**GDAL_COG_ENV), rasterio.open(href) as src:
                with WarpedVRT(src, crs=grid.crs, transform=grid.transform, width=grid.width,
                               height=grid.height, resampling=resampling, src_nodata=src_nodata,
                               nodata=src_nodata) as vrt:
                    return vrt.read(1)
        except rasterio.errors.RasterioIOError as e:
            last = e
            time.sleep(2 ** attempt)
    raise RuntimeError(f"Failed to read {href}: {last}") from last


def calibrate(dn: np.ndarray, scale: float | None, offset: float | None, nodata: float | None) -> np.ndarray:
    if scale is None or offset is None:
        raise CalibrationError("Asset lacks scale/offset metadata; refusing to guess calibration")
    out = dn.astype(np.float32) * np.float32(scale) + np.float32(offset)
    if nodata is not None:
        out[dn == nodata] = np.nan
    return out


def load_scene(scene: SceneMeta, grid: AnalysisGrid, bands: list[str] | None = None) -> dict[str, np.ndarray]:
    """Return {band: array} on the grid. Reflectance as float32 (NaN = no data); SCL as uint8 (0 = no data)."""
    bands = bands or ALL_BANDS
    missing = [b for b in bands if b not in scene.assets]
    if missing:
        raise ValueError(f"Scene {scene.scene_id} lacks bands {missing}")

    calib = {b: (scene.assets[b].scale, scene.assets[b].offset, scene.assets[b].nodata) for b in bands}
    k = cache.key("scene_bands", {"collection": scene.collection, "scene": scene.scene_id, "grid": grid.model_dump(),
                                  "bands": sorted(bands), "calib": calib})
    shape = (grid.height, grid.width)
    hit = cache.get_arrays(k, bands, shape)
    if hit is not None:
        return {b: hit[b] for b in bands}

    out: dict[str, np.ndarray] = {}
    for b in bands:
        a = scene.assets[b]
        nod = 0 if a.nodata is None else a.nodata
        if b == "SCL":
            out[b] = _read_on_grid(a.href, grid, Resampling.nearest, nod).astype(np.uint8)
        else:
            dn = _read_on_grid(a.href, grid, Resampling.bilinear, nod)
            out[b] = calibrate(dn, a.scale, a.offset, nod)
    cache.put_arrays(k, out)
    return out
