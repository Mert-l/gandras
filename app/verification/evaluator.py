"""Verification engine: deterministic rules mapping evidence to a verdict about the *exact* claim.

Rules (shown in each report's methodology):
- A claim is read as (indicator, asserts change | no_change, direction). The indicator's analysis
  yields `evidence_ha`: area of the indicator's evidence in the claimed direction.
- Data sufficiency: < 70% common valid area, or no usable pair -> inconclusive.
- evidence_ha >= indicator.significant_ha -> significant; < negligible_ha -> negligible; else ambiguous.
- Indicators flagged requires_persistence (surface disturbance, construction) count change only if an
  independent later observation confirms >= 60% of it. Change persisting < 30% is treated as transient
  for every indicator. VLM agreement only adjusts confidence; it never establishes change.
- "no_change" claims: change -> contradicted; negligible -> supported (confidence at most medium);
  otherwise inconclusive. "change" claims are mirrored.
- Generic fallback: can only be supported (no-change claim, negligible change) or inconclusive.
- Confidence (low/medium/high) is a points tally (valid >= 90%, seasonal match, persistence, VLM agreement,
  margin; minus one if the evidence class is unstable across observations), capped per indicator. No numeric probabilities.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from app.indicators.base import Indicator

ORDER = ["low", "medium", "high"]


class EvidenceThresholds(BaseModel):
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
    class_stable: bool | None = None
    needs_more_evidence: bool
    notes: list[str]


def assess(ind: Indicator, direction: str, analysis: dict | None, corroboration: dict | None, vlm: dict | None,
           th: EvidenceThresholds | None = None) -> Assessment:
    th = th or EvidenceThresholds()
    if not analysis:
        return Assessment(change_level="unknown", key_metric="none", key_value_ha=None, data_sufficient=False,
                          corroborated=None, vlm_agrees=None, needs_more_evidence=True, notes=["No completed analysis."])
    notes = list(ind.notes(analysis, direction))
    val = analysis["evidence_ha"]
    level = "significant" if val >= ind.significant_ha else "negligible" if val < ind.negligible_ha else "ambiguous"
    sufficient = analysis.get("common_valid_pct", 0) >= th.min_common_valid_pct
    if not sufficient:
        notes.append(f"Only {analysis.get('common_valid_pct', 0):.0f}% of the AOI is clear on both dates.")
    if not analysis["pair"]["seasonally_comparable"]:
        notes.append(f"Observations are {analysis['pair']['seasonal_gap_days']} days apart in season.")
    corroborated = None
    class_stable = None
    if corroboration and corroboration.get("found"):
        p = corroboration["persisting_fraction"]
        corroborated = True if p >= th.persistence_strong else False if p < th.persistence_weak else None
        notes.append(f"{p:.0%} of the evidence area persists in an independent observation.")
        sc = corroboration.get("same_class_fraction")
        if sc is not None and p >= th.persistence_strong:
            class_stable = sc >= th.persistence_weak
            if not class_stable:
                notes.append(f"The change persists, but only {sc:.0%} of it kept the '{ind.evidence_label}' classification in the "
                             "independent observation, so the type of change is less certain than its presence.")
    vlm_agrees = None
    if vlm and vlm.get("status") == "ok" and vlm.get("analysis"):
        c = vlm["analysis"].get("changes_consistent_with_measurements")
        vlm_agrees = True if c in ("yes", "partly") else False if c == "no" else None
    needs_more = (level in ("significant", "ambiguous") and corroborated is None) or not sufficient
    return Assessment(change_level=level, key_metric=f"evidence_ha ({ind.evidence_label})", key_value_ha=val,
                      data_sufficient=sufficient, corroborated=corroborated, vlm_agrees=vlm_agrees, class_stable=class_stable,
                      needs_more_evidence=needs_more, notes=notes)


def verdict(ind: Indicator, asserts: str, direction: str, analysis: dict | None, a: Assessment, vlm: dict | None) -> dict:
    rationale: list[str] = list(a.notes)
    if not analysis or not a.data_sufficient or a.change_level == "unknown":
        return _v("inconclusive", "low", "The available imagery is not sufficient to evaluate the claim.", rationale)

    big, small = a.change_level == "significant", a.change_level == "negligible"
    persuasive = (a.corroborated is True) if ind.requires_persistence else True
    if big and not persuasive:
        rationale.append(f"{ind.evidence_label.capitalize()} was measured, but it was not confirmed to persist in an "
                         "independent observation, so it cannot be treated as lasting change.")
    if a.corroborated is False:
        rationale.append("Most of the evidence did not persist in an independent observation, so it may be transient.")
    changed = big and persuasive and a.corroborated is not False

    if ind.is_fallback:
        status = "supported" if (asserts == "no_change" and small) else "inconclusive"
        rationale.append("No specific indicator matched this claim; general surface change cannot show whether a change "
                         "is the one the claim refers to, so the verdict is limited to supported or inconclusive.")
    elif asserts == "no_change":
        status = "contradicted" if changed else "supported" if small else "inconclusive"
    else:
        status = "supported" if changed else "contradicted" if small else "inconclusive"

    pts = 0
    pts += 1 if analysis["common_valid_pct"] >= 90 else 0
    pts += 1 if analysis["pair"]["seasonally_comparable"] else -1
    pts += 1 if a.corroborated is True else -1 if a.corroborated is False else 0
    pts += 1 if a.vlm_agrees is True else -1 if a.vlm_agrees is False else 0
    pts -= 1 if a.class_stable is False else 0
    if big and (a.key_value_ha or 0) >= 3 * ind.significant_ha:
        pts += 1
    if small and (a.key_value_ha or 0) == 0:
        pts += 1
    conf = "high" if pts >= 3 else "medium" if pts >= 1 else "low"
    if status == "inconclusive":
        conf = "low" if pts < 2 else "medium"
    if status == "supported" and asserts == "no_change":
        conf = min(conf, "medium", key=ORDER.index)
        rationale.append("A supported 'no change' finding means no qualifying change was detected at 10 m resolution; "
                         "it does not prove that nothing happened.")
    if ORDER.index(conf) > ORDER.index(ind.confidence_cap):
        conf = ind.confidence_cap
        rationale.append(f"Confidence is capped at {ind.confidence_cap} for {ind.name}: the change is measured, its meaning is inferred.")

    val, lab = a.key_value_ha or 0, ind.evidence_label
    dir_txt = {"increase": " (increase)", "decrease": " (decrease)"}.get(direction, "") if len(ind.directions) > 1 else ""
    if status == "contradicted":
        s = (f"Contradicted by available evidence: {val:.1f} ha of {lab}{dir_txt} was measured between the observation dates."
             if asserts == "no_change" else
             f"Contradicted by available evidence: only {val:.1f} ha of {lab}{dir_txt} was measured, below the {ind.negligible_ha:g} ha level.")
    elif status == "supported":
        s = (f"Supported by available evidence: {val:.1f} ha of {lab}{dir_txt} measured, below the {ind.significant_ha:g} ha significance level."
             if asserts == "no_change" else f"Supported by available evidence: {val:.1f} ha of {lab}{dir_txt} measured.")
    else:
        s = f"Inconclusive: {val:.1f} ha of {lab}{dir_txt} was measured, but the evidence does not settle the claim."
    return _v(status, conf, s, rationale)


def _v(status, conf, summary, rationale):
    return {"status": status, "confidence": conf, "summary": summary, "rationale": rationale}
