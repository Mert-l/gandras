"""Area of interest and the common projected analysis grid.

All before/after comparisons are made on one AnalysisGrid: a fixed UTM CRS,
affine transform, width and height. Pixel area is derived from the transform.
"""
from __future__ import annotations

import hashlib
import json
import math

from pydantic import BaseModel, Field, model_validator
from pyproj import CRS, Transformer
from rasterio.transform import Affine, from_origin


class AOI(BaseModel):
    """A square area of interest defined by a WGS84 centre and a width in metres."""

    name: str = "custom"
    lat: float = Field(ge=-84, le=84)  # UTM is undefined at the poles
    lon: float = Field(ge=-180, le=180)
    width_m: float = Field(gt=0)

    @model_validator(mode="after")
    def _limit(self) -> "AOI":
        if self.width_m > 50_000:
            raise ValueError("AOI width above 50 km is not allowed")
        return self

    @property
    def area_km2(self) -> float:
        return (self.width_m / 1000) ** 2

    def utm_epsg(self) -> int:
        zone = int((self.lon + 180) // 6) + 1
        return (32600 if self.lat >= 0 else 32700) + zone

    def bbox_wgs84(self) -> list[float]:
        """[min_lon, min_lat, max_lon, max_lat] enclosing the projected square."""
        g = self.grid(10.0)
        t = Transformer.from_crs(g.epsg, 4326, always_xy=True)
        xs, ys = [], []
        l, b, r, top = g.bounds
        for x, y in [(l, b), (l, top), (r, b), (r, top)]:
            lo, la = t.transform(x, y)
            xs.append(lo)
            ys.append(la)
        return [min(xs), min(ys), max(xs), max(ys)]

    def grid(self, resolution_m: float) -> "AnalysisGrid":
        epsg = self.utm_epsg()
        cx, cy = Transformer.from_crs(4326, epsg, always_xy=True).transform(self.lon, self.lat)
        n = max(1, int(round(self.width_m / resolution_m)))
        half = n * resolution_m / 2
        # Snap the origin to the resolution so grids are reproducible.
        left = math.floor((cx - half) / resolution_m) * resolution_m
        top = math.floor((cy + half) / resolution_m) * resolution_m
        return AnalysisGrid(epsg=epsg, left=left, top=top, resolution_m=resolution_m, width=n, height=n)


class AnalysisGrid(BaseModel):
    epsg: int
    left: float
    top: float
    resolution_m: float
    width: int
    height: int

    @property
    def crs(self) -> CRS:
        return CRS.from_epsg(self.epsg)

    @property
    def transform(self) -> Affine:
        return from_origin(self.left, self.top, self.resolution_m, self.resolution_m)

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        return (self.left, self.top - self.height * self.resolution_m,
                self.left + self.width * self.resolution_m, self.top)

    @property
    def pixel_area_m2(self) -> float:
        t = self.transform
        return abs(t.a * t.e - t.b * t.d)

    def key(self) -> str:
        return hashlib.sha1(json.dumps(self.model_dump(), sort_keys=True).encode()).hexdigest()[:12]
