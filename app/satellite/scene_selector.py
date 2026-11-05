"""Rank candidate scenes by local (AOI) quality, not just the scene-wide cloud figure."""
from __future__ import annotations

from datetime import date

from pydantic import BaseModel

from app.satellite.aoi import AnalysisGrid
from app.satellite.preprocessing import quality_stats
from app.satellite.raster_loader import load_scene
from app.satellite.stac_client import SceneMeta


class RankedScene(BaseModel):
    scene: SceneMeta
    quality: dict
    days_from_target: int
    usable: bool
    reason: str


def inspect_scene_quality(scene: SceneMeta, grid: AnalysisGrid) -> dict:
    """Local SCL statistics over the AOI (reads only the SCL band)."""
    scl = load_scene(scene, grid, bands=["SCL"])["SCL"]
    return quality_stats(scl)


def _seasonal_gap_days(a: date, b: date) -> int:
    d = abs(a.timetuple().tm_yday - b.timetuple().tm_yday)
    return min(d, 365 - d)


def rank_scenes(scenes: list[SceneMeta], grid: AnalysisGrid, target: date,
                max_invalid_pct: float, max_inspect: int = 8) -> list[RankedScene]:
    """Inspect the scenes nearest the target date and rank usable ones first.

    Scenes missing required bands are rejected. Among scenes of the same acquisition day
    (overlapping MGRS tiles), each is inspected; the tile covering the AOI wins on no_data_pct.
    """
    candidates = [s for s in scenes if not s.missing_bands()]
    candidates.sort(key=lambda s: (abs((s.acquired_at.date() - target).days), s.cloud_cover or 0))
    ranked: list[RankedScene] = []
    for s in candidates[:max_inspect]:
        q = inspect_scene_quality(s, grid)
        days = (s.acquired_at.date() - target).days
        usable = q["invalid_pct"] <= max_invalid_pct
        reason = ("local invalid {:.1f}% <= {:.0f}%" if usable else "local invalid {:.1f}% > {:.0f}%").format(
            q["invalid_pct"], max_invalid_pct)
        ranked.append(RankedScene(scene=s, quality=q, days_from_target=days, usable=usable, reason=reason))
    # Tiered: usable first; then essentially cloud-free (<=1% invalid) scenes, because SCL
    # under-flags shadows near clouds (seen at QB2, 2019-11-15); then date proximity.
    ranked.sort(key=lambda r: (not r.usable, r.quality["invalid_pct"] > 1, r.quality["invalid_pct"] > 5,
                               abs(r.days_from_target), r.quality["invalid_pct"]))
    return ranked


def describe_pair(before: SceneMeta, after: SceneMeta) -> dict:
    a, b = before.acquired_at.date(), after.acquired_at.date()
    gap = _seasonal_gap_days(a, b)
    return {"days_between": (b - a).days, "seasonal_gap_days": gap,
            "seasonally_comparable": gap <= 45,
            "same_tile": before.scene_id.split("_")[1] == after.scene_id.split("_")[1]}

