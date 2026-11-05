"""SCL masking, local quality statistics and consistent true-colour rendering."""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

# Sentinel-2 L2A Scene Classification values.
SCL_NAMES = {0: "no_data", 1: "saturated_defective", 2: "dark_area", 3: "cloud_shadow", 4: "vegetation",
             5: "not_vegetated", 6: "water", 7: "unclassified", 8: "cloud_medium", 9: "cloud_high",
             10: "thin_cirrus", 11: "snow_ice"}
INVALID_SCL = (0, 1, 3, 8, 9, 10, 11)
CLOUD_SCL = (8, 9, 10)


CLOUD_AND_SHADOW_SCL = (3, 8, 9, 10)


def valid_mask(scl: np.ndarray, invalid=INVALID_SCL, cloud_buffer_px: int = 5) -> np.ndarray:
    """Valid = not an invalid SCL class and not within cloud_buffer_px of cloud/shadow.

    The buffer catches cloud edges and shadow fringes that SCL commonly under-flags.
    """
    bad = np.isin(scl, invalid)
    if cloud_buffer_px > 0:
        import cv2
        cs = np.isin(scl, CLOUD_AND_SHADOW_SCL).astype(np.uint8)
        if cs.any():
            k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * cloud_buffer_px + 1,) * 2)
            bad |= cv2.dilate(cs, k).astype(bool)
    return ~bad


def quality_stats(scl: np.ndarray, invalid=INVALID_SCL) -> dict:
    n = scl.size
    pct = lambda m: round(100.0 * float(np.count_nonzero(m)) / n, 2)  # noqa: E731
    counts = {SCL_NAMES[v]: int(c) for v, c in zip(*np.unique(scl, return_counts=True))}
    return {
        "pixels": n,
        "no_data_pct": pct(scl == 0),
        "cloud_pct": pct(np.isin(scl, CLOUD_SCL)),
        "shadow_pct": pct(scl == 3),
        "snow_pct": pct(scl == 11),
        "invalid_pct": pct(np.isin(scl, invalid)),
        "valid_pct": pct(~np.isin(scl, invalid)),
        "scl_counts": counts,
        "invalid_classes": [SCL_NAMES[v] for v in invalid],
    }


def common_valid(*masks: np.ndarray) -> np.ndarray:
    out = masks[0].copy()
    for m in masks[1:]:
        out &= m
    return out


def shared_stretch(scenes: list[dict[str, np.ndarray]], masks: list[np.ndarray],
                   lo_pct: float = 2, hi_pct: float = 98) -> dict[str, tuple[float, float]]:
    """One (lo, hi) per RGB band, computed from pooled valid pixels of all scenes.

    The same stretch is then applied to every date so unchanged ground renders identically.
    """
    out = {}
    for b in ("B04", "B03", "B02"):
        pooled = np.concatenate([s[b][m & np.isfinite(s[b])] for s, m in zip(scenes, masks)])
        if pooled.size == 0:
            out[b] = (0.0, 0.3)
        else:
            lo, hi = np.percentile(pooled, [lo_pct, hi_pct])
            out[b] = (float(lo), float(max(hi, lo + 1e-3)))
    return out


def render_rgb(scene: dict[str, np.ndarray], stretch: dict[str, tuple[float, float]],
               mask: np.ndarray | None = None, gamma: float = 1.0) -> np.ndarray:
    chans = []
    for b in ("B04", "B03", "B02"):
        lo, hi = stretch[b]
        c = np.clip((scene[b] - lo) / (hi - lo), 0, 1)
        if gamma != 1.0:
            c = c ** (1 / gamma)
        chans.append(np.nan_to_num(c, nan=0.0))
    rgb = (np.dstack(chans) * 255).astype(np.uint8)
    if mask is not None:
        rgb[~mask] = (rgb[~mask] * 0.35 + np.array([90, 0, 90]) * 0.65).astype(np.uint8)  # tint invalid pixels
    return rgb


def save_png(rgb: np.ndarray, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rgb).save(path)
    return path
