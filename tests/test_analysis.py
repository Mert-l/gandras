"""Synthetic-array tests for NDVI, areas, region filtering and both change detectors."""
import numpy as np
import pytest

from app.analysis.change_detection import regions
from app.analysis.measurements import area_ha, mask_area_ha
from app.analysis.mining import analyze_surface_disturbance, bsi
from app.analysis.vegetation import VegetationThresholds, analyze_vegetation_change, ndvi
from app.satellite.aoi import AOI
from app.satellite.preprocessing import valid_mask

GRID = AOI(lat=-9.54, lon=-65.93, width_m=1000).grid(10)  # 100 x 100 px, 1 ha = 100 px
SHAPE = (GRID.height, GRID.width)


def _scene(red, nir, blue=0.03, swir=0.13, scl=4):
    f = lambda v: np.full(SHAPE, v, np.float32) if np.isscalar(v) else v.astype(np.float32)  # noqa: E731
    return {"B02": f(blue), "B03": f(0.05), "B04": f(red), "B08": f(nir), "B11": f(swir),
            "SCL": np.full(SHAPE, scl, np.uint8)}


def test_ndvi_known_values_and_zero_denominator():
    nd = ndvi(np.array([0.5, 0.3, 0.0, np.nan]), np.array([0.1, 0.3, 0.0, 0.1]))
    assert nd[0] == pytest.approx(0.4 / 0.6)
    assert nd[1] == pytest.approx(0.0)
    assert np.isnan(nd[2]) and np.isnan(nd[3])


def test_area_uses_grid_transform():
    assert GRID.pixel_area_m2 == pytest.approx(100)
    assert area_ha(100, GRID) == pytest.approx(1.0)
    assert area_ha(10_000, GRID) == pytest.approx(100.0)
    assert mask_area_ha(np.ones(SHAPE, bool), GRID) == pytest.approx(100.0)


def test_regions_drop_small_and_keep_large():
    m = np.zeros(SHAPE, bool)
    m[10:30, 10:30] = True   # 400 px = 4 ha
    m[60, 60] = True         # isolated pixel
    kept, regs = regions(m, GRID, min_area_ha=0.5)
    assert len(regs) == 1 and regs[0]["area_ha"] == pytest.approx(4.0)
    assert not kept[60, 60]
    assert -10 < regs[0]["centroid_lonlat"][1] < -9


def test_identical_images_have_no_change():
    s = _scene(0.03, 0.4)
    valid = np.ones(SHAPE, bool)
    assert analyze_vegetation_change(s, s, valid, GRID)["result"]["candidate_loss_ha"] == 0
    rng = np.random.default_rng(1)
    noisy = _scene(0.03 + rng.normal(0, 0.003, SHAPE), 0.4 + rng.normal(0, 0.003, SHAPE))
    assert analyze_surface_disturbance(noisy, noisy, valid, GRID)["result"]["candidate_changed_area_ha"] == 0


def test_forest_clearing_detected_with_indicators():
    before = _scene(0.025, 0.40, swir=0.13)                  # dense canopy, NDVI ~0.88
    red_a, nir_a, sw_a = (np.full(SHAPE, v, np.float32) for v in (0.025, 0.40, 0.13))
    red_a[20:50, 20:50], nir_a[20:50, 20:50], sw_a[20:50, 20:50] = 0.15, 0.20, 0.30  # 9 ha bare
    after = _scene(red_a, nir_a, swir=sw_a)
    res = analyze_vegetation_change(before, after, np.ones(SHAPE, bool), GRID)["result"]
    assert res["candidate_loss_ha"] == pytest.approx(9.0, abs=0.5)
    assert res["forest_like_loss_ha"] == pytest.approx(9.0, abs=0.5)
    assert res["probable_clearing_ha"] == pytest.approx(9.0, abs=0.5)
    assert set(res["regions"][0]["indicators"]) == {"ndvi_drop", "forest_like_before", "bare_soil_increase"}


def test_pasture_drying_is_loss_but_not_forest_like():
    before = _scene(0.07, 0.30, swir=0.25)                   # green pasture: NDVI ~0.62, bright SWIR
    after = _scene(0.15, 0.22, swir=0.32)                    # dry pasture
    res = analyze_vegetation_change(before, after, np.ones(SHAPE, bool), GRID)["result"]
    assert res["candidate_loss_ha"] > 50
    assert res["forest_like_loss_ha"] == 0


def test_invalid_pixels_never_count_as_change():
    before = _scene(0.025, 0.40)
    after = _scene(0.15, 0.20, swir=0.3)
    after["SCL"][:] = 9                                      # all cloud
    valid = valid_mask(before["SCL"]) & valid_mask(after["SCL"])
    assert analyze_vegetation_change(before, after, valid, GRID)["result"]["candidate_loss_ha"] == 0


def test_surface_disturbance_finds_new_bare_patch_and_normalizes_illumination():
    rng = np.random.default_rng(2)
    base = lambda v: v + rng.normal(0, 0.004, SHAPE)  # noqa: E731
    before = _scene(base(0.20), base(0.25), blue=0.12, swir=base(0.30))
    after = _scene(base(0.20) * 1.1, base(0.25) * 1.1, blue=0.13, swir=base(0.30) * 1.1)  # 10% brighter overall
    after["B04"][40:70, 40:70] = 0.40
    after["B11"][40:70, 40:70] = 0.55
    res = analyze_surface_disturbance(before, after, np.ones(SHAPE, bool), GRID)["result"]
    assert res["candidate_changed_area_ha"] == pytest.approx(9.0, abs=1.0)  # global brightening not flagged
    assert res["regions"][0]["spectral_pattern"] == "change_within_bare_ground"


def test_bsi_sign():
    s = _scene(0.3, 0.2, blue=0.1, swir=0.4)
    assert float(bsi(s)[0, 0]) > 0


def test_cloud_buffer_masks_fringe():
    scl = np.full((20, 20), 4, np.uint8)
    scl[10, 10] = 9
    assert valid_mask(scl, cloud_buffer_px=0).sum() == 399
    assert not valid_mask(scl, cloud_buffer_px=2)[10, 12]
