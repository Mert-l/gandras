"""Offline tests for AOI grids, calibration, SCL masking, rendering and the cache."""
from datetime import datetime, timezone

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from app.satellite import cache
from app.satellite.aoi import AOI
from app.satellite.preprocessing import (common_valid, quality_stats, render_rgb, shared_stretch,
                                         valid_mask)
from app.satellite.raster_loader import CalibrationError, _read_on_grid, calibrate
from app.satellite.scene_selector import describe_pair
from app.satellite.stac_client import BandAsset, SceneMeta
from rasterio.enums import Resampling


def test_grid_is_metric_and_centred():
    aoi = AOI(lat=40.523, lon=-112.150, width_m=5000)
    g = aoi.grid(10)
    assert g.epsg == 32612
    assert (g.width, g.height) == (500, 500)
    assert g.pixel_area_m2 == pytest.approx(100.0)
    lon0, lat0, lon1, lat1 = aoi.bbox_wgs84()
    assert (lon0 + lon1) / 2 == pytest.approx(-112.150, abs=1e-3)
    assert (lat0 + lat1) / 2 == pytest.approx(40.523, abs=1e-3)


def test_grid_is_deterministic():
    a = AOI(lat=-9.5, lon=-63.0, width_m=4000)
    assert a.grid(10).key() == a.grid(10).key()
    assert a.utm_epsg() == 32720  # southern hemisphere zone 20


def test_aoi_rejects_huge_or_invalid():
    with pytest.raises(ValueError):
        AOI(lat=0, lon=0, width_m=100_000)
    with pytest.raises(ValueError):
        AOI(lat=95, lon=0, width_m=1000)


def test_calibration_uses_metadata_and_nans_nodata():
    dn = np.array([[0, 1000], [2000, 11000]], dtype=np.uint16)
    r = calibrate(dn, 0.0001, -0.1, 0)
    assert np.isnan(r[0, 0])  # nodata is NaN, not zero reflectance
    assert r[0, 1] == pytest.approx(0.0, abs=1e-6)
    assert r[1, 1] == pytest.approx(1.0)
    with pytest.raises(CalibrationError):
        calibrate(dn, None, None, 0)


def test_scl_mask_and_quality():
    scl = np.array([[4, 5, 6, 8], [9, 10, 3, 0], [11, 1, 2, 7], [4, 4, 4, 4]], dtype=np.uint8)
    m = valid_mask(scl, cloud_buffer_px=0)
    assert m.sum() == 9  # 4,5,6,2,7 + four 4s
    q = quality_stats(scl)
    assert q["cloud_pct"] == pytest.approx(300 / 16, abs=0.01)
    assert q["invalid_pct"] + q["valid_pct"] == pytest.approx(100.0)


def test_common_valid_is_intersection():
    a = np.array([True, True, False])
    b = np.array([True, False, True])
    assert common_valid(a, b).tolist() == [True, False, False]


def test_shared_stretch_renders_identical_ground_identically():
    rng = np.random.default_rng(0)
    s = {b: rng.uniform(0.05, 0.3, (20, 20)).astype(np.float32) for b in ("B02", "B03", "B04")}
    other = {b: v.copy() for b, v in s.items()}
    other["B04"][:5] = 0.6  # change only a strip
    m = np.ones((20, 20), bool)
    st = shared_stretch([s, other], [m, m])
    a, b = render_rgb(s, st), render_rgb(other, st)
    assert np.array_equal(a[5:], b[5:])


def test_reprojection_keeps_scl_categorical(tmp_path):
    """Write a 20 m SCL raster and read it onto a 10 m grid: only original class values survive."""
    aoi = AOI(lat=40.5, lon=-112.1, width_m=2000)
    g = aoi.grid(10)
    src = tmp_path / "scl.tif"
    data = np.tile(np.array([[4, 9], [3, 6]], np.uint8), (60, 60))
    with rasterio.open(src, "w", driver="GTiff", width=120, height=120, count=1, dtype="uint8", crs=g.crs,
                       transform=from_origin(g.left - 200, g.top + 200, 20, 20), nodata=0) as dst:
        dst.write(data, 1)
    out = _read_on_grid(str(src), g, Resampling.nearest, 0)
    assert out.shape == (g.height, g.width)
    assert set(np.unique(out).tolist()) <= {4, 9, 3, 6}


def _scene(sid: str, d: datetime) -> SceneMeta:
    return SceneMeta(scene_id=sid, collection="c", acquired_at=d, cloud_cover=0, epsg=1, processing_baseline="05.00",
                     platform="x", self_href=None,
                     assets={"B04": BandAsset(band="B04", href="h", scale=1e-4, offset=-0.1, nodata=0, resolution_m=10)})


def test_describe_pair_seasonality():
    b = _scene("S2A_T12TUK_x", datetime(2019, 7, 12, tzinfo=timezone.utc))
    a = _scene("S2B_T12TVK_y", datetime(2024, 1, 10, tzinfo=timezone.utc))
    d = describe_pair(b, a)
    assert d["seasonally_comparable"] is False and d["same_tile"] is False
    assert describe_pair(b, _scene("S2A_T12TUK_z", datetime(2024, 7, 20, tzinfo=timezone.utc)))["seasonally_comparable"]
    assert b.missing_bands()  # only B04 present


def test_cache_rejects_wrong_shape(tmp_path, monkeypatch):
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "cache_dir", tmp_path)
    k = cache.key("t", {"a": 1})
    cache.put_arrays(k, {"B04": np.zeros((3, 3), np.float32)})
    assert cache.get_arrays(k, ["B04"], (3, 3)) is not None
    assert cache.get_arrays(k, ["B04"], (4, 4)) is None
    assert cache.get_arrays(k, ["B04", "SCL"], (3, 3)) is None
