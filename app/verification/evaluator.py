"""Verification engine: deterministic rules that map evidence to a verdict about the *exact* claim.

Rules (documented in the report's methodology):
- Claims are typed: "no_change" assertions (no forest loss / no expansion) or "change" assertions.
- Data sufficiency: < 70% common valid area, or no usable pair -> inconclusive.
- ForestCheck evidence of clearing = `probable_clearing_ha` (NDVI drop from forest-like canopy AND
  bare-soil increase). General vegetation loss alone (e.g. pasture drying) is never treated as clearing.
- MineWatch evidence = `candidate_changed_area_ha`. Surface change alone is not expansion: a
  "contradicted"/"supported-change" finding additionally needs corroboration that the change persists
  (>= 60%) or a VLM reading consistent with the measurements. MineWatch confidence is capped at medium.
- Significant / negligible thresholds are configurable (EvidenceThresholds).
- Confidence (low/medium/high) is a points tally, not a probability: data quality, seasonal match,
  corroboration, VLM agreement, margin above the significance threshold.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

NO_CHANGE = {"no_forest_loss", "no_expansion"}
CHANGE = {"forest_loss_occurred", "expansion_occurred"}


class EvidenceThresholds(BaseModel):
    forest_significant_ha: float = 5.0
    forest_negligible_ha: float = 1.0
    mining_significant_ha: float = 10.0
    mining_negligible_ha: float = 2.0
    min_common_valid_pct: float = 70.0
    persistence_strong: float = 0.6
    persistence_weak: float = 0.3


class Assessment(BaseModel):
    change_level: Literal["significant", "negligible", "ambiguous", "unknown"]
    key_metric: str
    key_value_ha: float | None
    data_sufficient: bool
    corroborated: bool | None
    vlm_agrees: bool | None
    needs_more_evidence: bool
    notes: list[str]


def assess(mode: str, analysis: dict | None, corroboration: dict | None, vlm: dict | None,
           th: EvidenceThresholds | None = None) -> Assessment:
    th = th or EvidenceThresholds()
    notes: list[str] = []
    if not analysis:
        return Assessment(change_level="unknown", key_metric="none", key_value_ha=None, data_sufficient=False,
                          corroborated=None, vlm_agrees=None, needs_more_evidence=True, notes=["No completed analysis."])
    if mode == "forest":
        key, val = "probable_clearing_ha", analysis["probable_clearing_ha"]
        sig, neg = th.forest_significant_ha, th.forest_negligible_ha
        if analysis["candidate_loss_ha"] >= sig and val < sig:
            notes.append(f"{analysis['candidate_loss_ha']:.1f} ha of general vegetation-index loss, but only {val:.1f} ha "
                         "was forest-like canopy with a bare-soil increase; the rest may be pasture, crops or seasonal change.")
    else:
        key, val = "candidate_changed_area_ha", analysis["candidate_changed_area_ha"]
        sig, neg = th.mining_significant_ha, th.mining_negligible_ha
    level = "significant" if val >= sig else "negligible" if val < neg else "ambiguous"

    sufficient = analysis.get("common_valid_pct", 0) >= th.min_common_valid_pct
    if not sufficient:
        notes.append(f"Only {analysis.get('common_valid_pct', 0):.0f}% of the AOI is clear on both dates.")
    if not analysis["pair"]["seasonally_comparable"]:
        notes.append(f"Observations are {analysis['pair']['seasonal_gap_days']} days apart in season.")

    corroborated = None
    if corroboration and corroboration.get("found"):
        p = corroboration["persisting_fraction"]
        corroborated = p >= th.persistence_strong if (p >= th.persistence_strong or p < th.persistence_weak) else None
        notes.append(f"{p:.0%} of detected change persists in an independent observation.")

    vlm_agrees = None
    if vlm and vlm.get("status") == "ok" and vlm.get("analysis"):
        c = vlm["analysis"].get("changes_consistent_with_measurements")
        vlm_agrees = True if c in ("yes", "partly") else False if c == "no" else None

    needs_more = (level in ("significant", "ambiguous") and corroborated is None) or not sufficient
    return Assessment(change_level=level, key_metric=key, key_value_ha=val, data_sufficient=sufficient,
                      corroborated=corroborated, vlm_agrees=vlm_agrees, needs_more_evidence=needs_more, notes=notes)


def verdict(mode: str, claim_type: str, analysis: dict | None, a: Assessment, vlm: dict | None,
            th: EvidenceThresholds | None = None) -> dict:
    th = th or EvidenceThresholds()
    rationale: list[str] = list(a.notes)
    if not analysis or not a.data_sufficient or a.change_level == "unknown":
        return _v("inconclusive", "low", "The available imagery is not sufficient to evaluate the claim.", rationale)

    big = a.change_level == "significant"
    small = a.change_level == "negligible"
    persuasive = (a.corroborated is True) or (a.vlm_agrees is True) if mode == "mining" else True
    if mode == "mining" and big and not persuasive:
        rationale.append("Significant surface change was measured, but neither persistence checks nor visual "
                         "interpretation confirm it is expansion-related.")
    if a.corroborated is False:
        rationale.append("Most detected change did not persist in an independent observation, so it may be transient.")

    changed = big and persuasive and a.corroborated is not False
    if claim_type in NO_CHANGE:
        status = "contradicted" if changed else "supported" if small else "inconclusive"
    else:
        status = "supported" if changed else "contradicted" if small else "inconclusive"

    # Confidence points
    pts = 0
    pts += 1 if analysis["common_valid_pct"] >= 90 else 0
    pts += 1 if analysis["pair"]["seasonally_comparable"] else -1
    pts += 1 if a.corroborated is True else -1 if a.corroborated is False else 0
    pts += 1 if a.vlm_agrees is True else -1 if a.vlm_agrees is False else 0
    sig = th.forest_significant_ha if mode == "forest" else th.mining_significant_ha
    if big and (a.key_value_ha or 0) >= 3 * sig:
        pts += 1
    if small and (a.key_value_ha or 0) == 0:
        pts += 1
    conf = "high" if pts >= 3 else "medium" if pts >= 1 else "low"
    if status == "inconclusive":
        conf = "low" if pts < 2 else "medium"
    if status == "supported" and claim_type in NO_CHANGE:
        conf = min(conf, "medium", key=["low", "medium", "high"].index)  # absence of evidence is weaker
        rationale.append("A supported 'no change' finding means no qualifying change was detected at 10 m resolution; "
                         "it does not prove that nothing happened.")

    if mode == "mining" and conf == "high":
        conf = "medium"  # spectral change cannot by itself establish that disturbance is mine expansion
        rationale.append("MineWatch confidence is capped at medium: surface disturbance is measured, expansion is inferred.")

    label = "probable forest clearing" if mode == "forest" else "candidate surface disturbance"
    val = a.key_value_ha or 0
    summary = {
        "contradicted": f"Contradicted by available evidence: {val:.1f} ha of {label} was measured between the observation dates.",
        "supported": (f"Supported by available evidence: {val:.1f} ha of {label} measured, below the {sig:.0f} ha significance level."
                      if claim_type in NO_CHANGE else f"Supported by available evidence: {val:.1f} ha of {label} measured."),
        "inconclusive": f"Inconclusive: {val:.1f} ha of {label} was measured, but the evidence does not settle the claim.",
    }[status]
    return _v(status, conf, summary, rationale)


def _v(status, conf, summary, rationale):
    return {"status": status, "confidence": conf, "summary": summary, "rationale": rationale}
