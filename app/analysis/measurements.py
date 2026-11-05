"""Area arithmetic derived from the real analysis grid (never from PNG dimensions)."""
from __future__ import annotations

import numpy as np

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.satellite.aoi import AnalysisGrid


def area_ha(pixel_count: int | float, grid: AnalysisGrid) -> float:
    return float(pixel_count) * grid.pixel_area_m2 / 10_000.0


def mask_area_ha(mask: np.ndarray, grid: AnalysisGrid) -> float:
    return area_ha(int(np.count_nonzero(mask)), grid)


def safe_fraction(num: float, den: float) -> float | None:
    return None if den <= 0 else num / den
