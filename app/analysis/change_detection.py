"""Spatial filtering of candidate-change masks into measurable connected regions."""
from __future__ import annotations

import cv2
import numpy as np
from pyproj import Transformer

from app.analysis.measurements import area_ha
from app.satellite.aoi import AnalysisGrid


def clean_mask(mask: np.ndarray, open_px: int = 1) -> np.ndarray:
    """Morphological opening to remove isolated pixels (3x3 kernel by default)."""
    if open_px <= 0:
        return mask.copy()
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * open_px + 1, 2 * open_px + 1))
    return cv2.morphologyEx(mask.astype(np.uint8), cv2.MORPH_OPEN, k).astype(bool)


def regions(mask: np.ndarray, grid: AnalysisGrid, min_area_ha: float,
            layers: dict[str, np.ndarray] | None = None, max_regions: int = 50) -> tuple[np.ndarray, list[dict]]:
    """Label 8-connected components, drop those below min_area_ha, and describe the rest.

    Returns (kept_mask, regions sorted by area desc). `layers` adds per-region mean values
    (e.g. delta_ndvi) computed over the region's pixels, ignoring NaN.
    """
    n, labels, stats, cents = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
    to_wgs = Transformer.from_crs(grid.epsg, 4326, always_xy=True)
    t = grid.transform
    min_px = min_area_ha * 10_000 / grid.pixel_area_m2
    kept = np.zeros_like(mask, dtype=bool)
    out: list[dict] = []
    for i in range(1, n):
        px = int(stats[i, cv2.CC_STAT_AREA])
        if px < min_px:
            continue
        sel = labels == i
        kept |= sel
        x, y, w, h = (int(stats[i, k]) for k in (cv2.CC_STAT_LEFT, cv2.CC_STAT_TOP, cv2.CC_STAT_WIDTH, cv2.CC_STAT_HEIGHT))
        cx, cy = cents[i]
        mx, my = t * (cx + 0.5, cy + 0.5)
        lon, lat = to_wgs.transform(mx, my)
        r = {
            "area_ha": round(area_ha(px, grid), 3),
            "pixels": px,
            "bbox_px": [x, y, w, h],
            "centroid_px": [round(float(cx), 1), round(float(cy), 1)],
            "centroid_lonlat": [round(lon, 6), round(lat, 6)],
            "extent_m": [w * grid.resolution_m, h * grid.resolution_m],
            "fill_ratio": round(px / float(w * h), 3),  # 1 = compact block, low = linear/irregular
            "elongation": round(max(w, h) / max(1, min(w, h)), 2),
        }
        for name, arr in (layers or {}).items():
            v = arr[sel]
            v = v[np.isfinite(v)]
            r[f"mean_{name}"] = round(float(v.mean()), 4) if v.size else None
        out.append(r)
    out.sort(key=lambda r: r["area_ha"], reverse=True)
    for idx, r in enumerate(out, 1):
        r["id"] = idx
    return kept, out[:max_regions]
