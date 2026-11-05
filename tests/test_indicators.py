"""Synthetic-array tests for the new indicators and claim routing."""
import numpy as np
import pytest

from app.agent.claims import rules_reading
from app.indicators import REGISTRY, get
from app.satellite.aoi import AOI

GRID = AOI(lat=36.1, lon=-114.8, width_m=1000).grid(10)   # 100x100 px; 100 px = 1 ha
SHAPE = (GRID.height, GRID.width)
VALID = np.ones(SHAPE, bool)


def scene(**bands):
    base = {"B02": 0.05, "B03": 0.07, "B04": 0.06, "B08": 0.30, "B11": 0.18, "B12": 0.10}
    base.update(bands)
    out = {k: (np.full(SHAPE, v, np.float32) if np.isscalar(v) else v.astype(np.float32)) for k, v in base.items()}
    out["SCL"] = np.full(SHAPE, 4, np.uint8)
    return out


def patch(arr_value, inside, rows=slice(30, 60), cols=slice(30, 60)):
    a = np.full(SHAPE, arr_value, np.float32)
    a[rows, cols] = inside
    return a


def test_registry_has_one_fallback_and_valid_thresholds():
    assert sum(i.is_fallback for i in REGISTRY.values()) == 1
    for i in REGISTRY.values():
        assert i.negligible_ha < i.significant_ha and i.default_direction in i.directions


def test_water_loss_measured_and_direction():
    before = scene(B03=patch(0.08, 0.10), B11=patch(0.20, 0.02))           # lake in the patch (MNDWI > 0)
    after = scene()                                                         # lake gone
    r = get("water").analyze(before, after, VALID, GRID, "decrease")["result"]
    assert r["water_before_ha"] == pytest.approx(9.0, abs=0.5) and r["water_after_ha"] == 0
    assert r["evidence_ha"] == pytest.approx(9.0, abs=0.5) and r["lost_water_ha"] == pytest.approx(9.0, abs=0.5)
    assert get("water").analyze(before, after, VALID, GRID, "increase")["result"]["evidence_ha"] == 0


def test_built_up_detects_vegetation_to_construction():
    rng = np.random.default_rng(0)
    noise = lambda v: v + rng.normal(0, 0.003, SHAPE)  # noqa: E731
    before = scene(B04=noise(0.04), B08=noise(0.35), B11=noise(0.16))       # vegetated field
    after = scene(B04=noise(0.04), B08=noise(0.35), B11=noise(0.16))
    roof = after
    # new roof, spectra like the real Giga Berlin hall roof (low BSI after)
    for b, v in {"B02": .22, "B03": .23, "B04": .24, "B08": .27, "B11": .25}.items():
        roof[b][30:60, 30:60] = v
    r = get("built_up").analyze(before, roof, VALID, GRID, "increase")["result"]
    assert r["evidence_ha"] == pytest.approx(9.0, abs=1.0)
    assert r["regions"][0]["pattern"] == "vegetation_to_construction"


def test_built_up_bare_soil_clearing_is_not_evidence():
    """Vegetation scraped to bare soil (mine/earthworks) goes to amber, not to building evidence."""
    rng = np.random.default_rng(0)
    noise = lambda v: v + rng.normal(0, 0.003, SHAPE)  # noqa: E731
    before = scene(B04=noise(0.04), B08=noise(0.35), B11=noise(0.16))
    after = scene(B04=noise(0.04), B08=noise(0.35), B11=noise(0.16))
    for b, v in {"B02": .15, "B04": .20, "B08": .24, "B11": .30}.items():
        after[b][30:60, 30:60] = v
    r = get("built_up").analyze(before, after, VALID, GRID, "increase")["result"]
    assert r["evidence_ha"] == 0
    assert r["cleared_or_earthworks_ha"] == pytest.approx(9.0, abs=1.0)


def test_built_up_ignores_identical_scenes_and_new_water():
    s = scene()
    assert get("built_up").analyze(s, s, VALID, GRID, "increase")["result"]["evidence_ha"] == 0
    flooded = scene(B03=patch(0.07, 0.10), B11=patch(0.18, 0.02), B08=patch(0.30, 0.02), B04=patch(0.06, 0.04))
    assert get("built_up").analyze(s, flooded, VALID, GRID, "increase")["result"]["evidence_ha"] == 0


def test_burn_scar_detected():
    before = scene(B08=0.35, B12=0.10, B04=0.04)
    after = scene(B08=patch(0.35, 0.12), B12=patch(0.10, 0.25), B04=patch(0.04, 0.08))
    r = get("burn").analyze(before, after, VALID, GRID, "increase")["result"]
    assert r["evidence_ha"] == pytest.approx(9.0, abs=0.5)


def test_vegetation_gain_direction():
    before = scene(B04=patch(0.06, 0.15), B08=patch(0.30, 0.18))            # bare patch
    after = scene()                                                          # vegetated
    v = get("vegetation_cover")
    assert v.analyze(before, after, VALID, GRID, "increase")["result"]["evidence_ha"] == pytest.approx(9.0, abs=0.5)
    assert v.analyze(before, after, VALID, GRID, "decrease")["result"]["evidence_ha"] == 0


@pytest.mark.parametrize("claim,ind,asserts,direction", [
    ("No forest was cleared in this area after 2020.", "forest_clearing", "no_change", "decrease"),
    ("The mining operation at this site stopped expanding after 2019.", "surface_disturbance", "no_change", "any"),
    ("A building was illegally built in this protected area after 2020.", "built_up", "change", "increase"),
    ("The factory on this site was completed by 2022.", "built_up", "change", "increase"),
    ("No construction took place on this site after 2019.", "built_up", "no_change", "increase"),
    ("The reservoir has not shrunk since 2019.", "water", "no_change", "decrease"),
    ("The lake dried up between 2020 and 2023.", "water", "change", "decrease"),
    ("No fires burned here in 2023.", "burn", "no_change", "increase"),
    ("The restoration project has re-vegetated this area since 2019.", "vegetation_cover", "change", "increase"),
    ("Nothing has changed at this site since 2020.", "generic_change", "no_change", "any"),
])
def test_rule_routing(claim, ind, asserts, direction):
    r, _ = rules_reading(claim)
    assert (r.indicator, r.asserts, r.direction) == (ind, asserts, direction)
