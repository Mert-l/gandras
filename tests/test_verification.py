from app.indicators import get
from app.verification.evaluator import assess, verdict

FOREST, MINE, BUILT, GENERIC, WATER = (get(i) for i in ("forest_clearing", "surface_disturbance", "built_up", "generic_change", "water"))


def _a(ha=0.0, valid=99.0, seasonal=True, loss=None):
    return {"common_valid_pct": valid, "evidence_ha": ha, "probable_clearing_ha": ha,
            "candidate_loss_ha": ha if loss is None else loss,
            "pair": {"seasonally_comparable": seasonal, "seasonal_gap_days": 0 if seasonal else 150}}


def _v(ind, asserts, a, corr=None, vlm=None, direction=None):
    d = direction or ind.default_direction
    return verdict(ind, asserts, d, a, assess(ind, d, a, corr, vlm), vlm)


STRONG = {"found": True, "persisting_fraction": 0.9}
YES = {"status": "ok", "analysis": {"changes_consistent_with_measurements": "yes"}}


def test_forest_clearing_contradicts_no_loss_claim():
    v = _v(FOREST, "no_change", _a(50), STRONG)
    assert v["status"] == "contradicted" and v["confidence"] == "high"


def test_pasture_drying_alone_does_not_contradict():
    v = _v(FOREST, "no_change", _a(0.5, loss=200))
    assert v["status"] in ("supported", "inconclusive")
    assert any("pasture" in r for r in v["rationale"])


def test_poor_coverage_is_inconclusive():
    assert _v(FOREST, "no_change", _a(50, valid=40))["status"] == "inconclusive"


def test_persistence_required_indicators():
    for ind in (MINE, BUILT):
        assert _v(ind, "no_change", _a(60), None, YES)["status"] == "inconclusive"   # VLM alone is not enough
        v = _v(ind, "no_change", _a(60), STRONG, YES)
        assert v["status"] == "contradicted" and v["confidence"] == "medium"        # capped


def test_construction_claim_supported_by_persistent_new_built_surface():
    v = _v(BUILT, "change", _a(25), STRONG, YES)
    assert v["status"] == "supported"


def test_no_construction_claim_supported_when_nothing_built():
    v = _v(BUILT, "no_change", _a(0))
    assert v["status"] == "supported" and v["confidence"] in ("low", "medium")


def test_transient_change_weakens_finding():
    assert _v(FOREST, "no_change", _a(50), {"found": True, "persisting_fraction": 0.1})["status"] == "inconclusive"


def test_change_claim_polarity():
    assert _v(FOREST, "change", _a(0))["status"] == "contradicted"
    assert _v(FOREST, "change", _a(50), STRONG)["status"] == "supported"


def test_generic_fallback_limited():
    assert _v(GENERIC, "no_change", _a(80), STRONG, YES)["status"] == "inconclusive"
    assert _v(GENERIC, "change", _a(80), STRONG, YES)["status"] == "inconclusive"
    v = _v(GENERIC, "no_change", _a(0))
    assert v["status"] == "supported" and v["confidence"] == "low"


def test_water_direction_in_summary():
    v = _v(WATER, "no_change", _a(30), STRONG, direction="decrease")
    assert v["status"] == "contradicted" and "(decrease)" in v["summary"]


def test_seasonal_mismatch_lowers_confidence():
    order = ["low", "medium", "high"]
    a = _v(FOREST, "no_change", _a(50), STRONG)["confidence"]
    b = _v(FOREST, "no_change", _a(50, seasonal=False), STRONG)["confidence"]
    assert order.index(b) < order.index(a)



def test_unstable_classification_noted_and_lowers_confidence():
    stable = _v(FOREST, "no_change", _a(10), {"found": True, "persisting_fraction": 0.9, "same_class_fraction": 0.9})
    wobbly = _v(FOREST, "no_change", _a(10), {"found": True, "persisting_fraction": 0.9, "same_class_fraction": 0.05})
    assert wobbly["status"] == "contradicted"
    assert any("classification" in r for r in wobbly["rationale"])
    assert ["low", "medium", "high"].index(wobbly["confidence"]) < ["low", "medium", "high"].index(stable["confidence"])
