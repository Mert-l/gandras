"""Render analysis products as PNGs. These are for viewing only; measurements use the arrays."""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
from PIL import Image


def _save(arr: np.ndarray, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(arr).save(path)
    return path


def overlay(rgb: np.ndarray, change: np.ndarray, regions: list[dict], path: Path,
            color=(255, 40, 40), label_top: int = 12) -> Path:
    """Base RGB with change pixels tinted, region outlines and numbered labels."""
    out = rgb.copy()
    out[change] = (out[change] * 0.45 + np.array(color) * 0.55).astype(np.uint8)
    cnts, _ = cv2.findContours(change.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cv2.drawContours(out, cnts, -1, (255, 230, 0), 1)
    for r in regions[:label_top]:
        x, y = (int(v) for v in r["centroid_px"])
        cv2.putText(out, str(r["id"]), (x + 4, y - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 0), 3, cv2.LINE_AA)
        cv2.putText(out, str(r["id"]), (x + 4, y - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1, cv2.LINE_AA)
    return _save(out, path)


def mask_rgba(change: np.ndarray, path: Path, color=(255, 40, 40), alpha: int = 150) -> Path:
    """Transparent PNG of the change mask, to layer on top of RGB in the UI."""
    out = np.zeros((*change.shape, 4), np.uint8)
    out[change] = (*color, alpha)
    edges = cv2.Canny(change.astype(np.uint8) * 255, 50, 150) > 0
    out[edges] = (255, 230, 0, 255)
    return _save(out, path)


def _ramp(v: np.ndarray, stops: list[tuple[float, tuple[int, int, int]]]) -> np.ndarray:
    xs = [s[0] for s in stops]
    out = np.zeros((*v.shape, 3), np.uint8)
    vv = np.nan_to_num(v, nan=xs[0])
    for c in range(3):
        out[..., c] = np.interp(vv, xs, [s[1][c] for s in stops]).astype(np.uint8)
    out[~np.isfinite(v)] = (60, 60, 60)
    return out


def ndvi_png(nd: np.ndarray, path: Path) -> Path:
    return _save(_ramp(nd, [(-0.2, (120, 80, 40)), (0.2, (230, 220, 160)), (0.5, (120, 190, 80)), (0.9, (10, 90, 30))]), path)


def delta_png(delta: np.ndarray, path: Path, span: float = 0.6) -> Path:
    return _save(_ramp(delta, [(-span, (200, 20, 20)), (0.0, (240, 240, 240)), (span, (20, 120, 200))]), path)


def side_by_side(a: np.ndarray, b: np.ndarray, path: Path) -> Path:
    gap = np.full((a.shape[0], 8, 3), 255, np.uint8)
    return _save(np.hstack([a, gap, b]), path)
