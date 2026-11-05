from app.verification.evaluator import assess, verdict


def _a(mode="forest", ha=0.0, valid=99.0, seasonal=True, loss=None):
    a = {"common_valid_pct": valid, "pair": {"seasonally_comparable": seasonal, "seasonal_gap_days": 0 if seasonal else 150}}
    if mode == "forest":
        a.update(probable_clearing_ha=ha, candidate_loss_ha=ha if loss is None else loss, forest_like_loss_ha=ha)
    else:
        a["candidate_changed_area_ha"] = ha
    return a


def _v(mode, ctype, a, corr=None, vlm=None):
    return verdict(mode, ctype, a, assess(mode, a, corr, vlm), vlm)


def test_forest_clearing_contradicts_no_loss_claim():
    v = _v("forest", "no_forest_loss", _a(ha=50), {"found": True, "persisting_fraction": 0.9})
    assert v["status"] == "contradicted" and v["confidence"] == "high"


def test_pasture_drying_alone_does_not_contradict():
    v = _v("forest", "no_forest_loss", _a(ha=0.5, loss=200))
    assert v["status"] == "supported" or v["status"] == "inconclusive"
    assert any("pasture" in r for r in v["rationale"])


def test_poor_coverage_is_inconclusive():
    assert _v("forest", "no_forest_loss", _a(ha=50, valid=40))["status"] == "inconclusive"


def test_mining_change_without_support_is_inconclusive():
    v = _v("mining", "no_expansion", _a("mining", ha=60))
    assert v["status"] == "inconclusive"


def test_mining_vlm_alone_not_enough():
    vlm = {"status": "ok", "analysis": {"changes_consistent_with_measurements": "yes"}}
    assert _v("mining", "no_expansion", _a("mining", ha=60), None, vlm)["status"] == "inconclusive"


def test_mining_confidence_capped_at_medium():
    vlm = {"status": "ok", "analysis": {"changes_consistent_with_measurements": "yes"}}
    v = _v("mining", "no_expansion", _a("mining", ha=60), {"found": True, "persisting_fraction": 0.9}, vlm)
    assert v["status"] == "contradicted" and v["confidence"] == "medium"


def test_transient_change_weakens_finding():
    v = _v("forest", "no_forest_loss", _a(ha=50), {"found": True, "persisting_fraction": 0.1})
    assert v["status"] == "inconclusive"


def test_change_claim_polarity():
    assert _v("forest", "forest_loss_occurred", _a(ha=0))["status"] == "contradicted"
    assert _v("forest", "forest_loss_occurred", _a(ha=50), {"found": True, "persisting_fraction": 0.8})["status"] == "supported"


def test_seasonal_mismatch_lowers_confidence():
    a, b = _a(ha=50), _a(ha=50, seasonal=False)
    c = {"found": True, "persisting_fraction": 0.9}
    order = ["low", "medium", "high"]
    assert order.index(_v("forest", "no_forest_loss", b, c)["confidence"]) < order.index(_v("forest", "no_forest_loss", a, c)["confidence"])
