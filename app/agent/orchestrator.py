"""Bounded investigation loop. Every timeline step corresponds to an executed action."""
from __future__ import annotations

import logging
import time
import traceback
from datetime import date, timedelta

from app import database
from app.agent import planner, tools
from app.agent.state import InvestigationState, Step
from app.config import get_settings
from app.database import now
from app.satellite import cache
from app.verification import evaluator, report

log = logging.getLogger(__name__)

PROGRESS = {"interpret_claim": 8, "find_before_observation": 22, "find_after_observation": 36,
            "search_more_imagery": None, "analyze_vegetation": 55, "analyze_surface": 55, "interpret_images": 72,
            "corroborate_change": 85, "evaluate_evidence": 90, "finalize": 100}
COUNTED = {"find_before_observation", "find_after_observation", "search_more_imagery", "analyze_vegetation",
           "analyze_surface", "interpret_images", "corroborate_change", "evaluate_evidence"}


class Runner:
    def __init__(self, st: InvestigationState, use_llm: bool = True):
        self.st = st
        s = get_settings()
        self.max_steps = int(st.options.get("max_agent_steps") or s.max_agent_steps)
        self.max_retries = s.max_imagery_retries if st.options.get("allow_additional_imagery", True) else 0
        self.use_llm = use_llm

    # ------------------------------------------------------------ bookkeeping
    def _save(self):
        database.save(self.st.model_dump(mode="json"))

    def _begin(self, action, reason, decided_by, details=None) -> Step:
        step = Step(index=len(self.st.steps) + 1, action=action, reason=reason, decided_by=decided_by,
                    summary="Running…", details=details or {})
        self.st.steps.append(step)
        self.st.current_step = action
        self._save()
        return step

    def _end(self, step: Step, summary: str, details: dict | None = None, ok: bool = True):
        step.status = "completed" if ok else "failed"
        step.summary = summary
        step.finished_at = now()
        if details:
            step.details.update(details)
        p = PROGRESS.get(step.action)
        if p:
            self.st.progress_percent = max(self.st.progress_percent, p)
        self._save()

    # ------------------------------------------------------------ main loop
    def run(self) -> InvestigationState:
        st = self.st
        st.status = "running"
        stats0 = dict(cache.STATS)
        self._save()
        try:
            if not self._interpret_claim():
                return st
            while True:
                allowed = planner.allowed_actions(st, self.max_retries)
                if st.tool_call_count >= self.max_steps:
                    self._finalize("rule", f"Tool-call budget of {self.max_steps} reached.", budget_hit=True)
                    break
                action, reason, params, by = self._decide(allowed)
                if action == "finalize":
                    self._finalize(by, reason)
                    break
                self._execute(action, reason, params, by)
            st.from_cache = cache.STATS["misses"] == stats0["misses"] and cache.STATS["hits"] > stats0["hits"]
            st.status = "completed"
        except tools.ToolError as e:
            self._fail(e.code, str(e))
        except Exception as e:  # noqa: BLE001 - execution failure, reported as such
            log.error("investigation %s failed: %s", st.investigation_id, traceback.format_exc())
            msg = str(e)
            code = "SATELLITE_DOWNLOAD_FAILED" if ("STAC" in msg or "Failed to read" in msg) else "ANALYSIS_FAILED"
            self._fail(code, f"{type(e).__name__}: {msg[:300]}")
        st.current_step = None
        self._save()
        return st

    def _fail(self, code: str, msg: str):
        st = self.st
        for s in st.steps:
            if s.status == "running":
                s.status, s.summary, s.finished_at = "failed", msg, now()
        st.status = "failed"
        st.error = {"code": code, "message": msg}
        st.errors.append(f"{code}: {msg}")

    def _decide(self, allowed: list[str]):
        if len(allowed) == 1:
            a, r, p = planner.rule_decision(self.st, allowed)
            return a, r, p, "rule"
        if self.use_llm:
            d = planner.llm_decision(self.st, allowed, self.max_steps)
            if d:
                return (*d, "llm")
        a, r, p = planner.rule_decision(self.st, allowed)
        return a, (r + " (rule fallback)" if self.use_llm else r), p, "rule"

    # ------------------------------------------------------------ steps
    def _interpret_claim(self) -> bool:
        st = self.st
        step = self._begin("interpret_claim", "Every investigation starts by typing the claim.", "system")
        ci, by = (planner.interpret_claim_llm(st.mode, st.claim), "llm") if self.use_llm else (None, "rule")
        valid = {"forest": {"no_forest_loss", "forest_loss_occurred"}, "mining": {"no_expansion", "expansion_occurred"}}[st.mode]
        override = None
        if ci is None:
            ci, by = planner.interpret_claim_rules(st.mode, st.claim), "rule"
        elif (not ci.supported or ci.claim_type not in valid) and planner.is_on_topic(st.mode, st.claim):
            # A small model must not unilaterally reject a claim that names the surface features
            # this mode measures; fall back to the deterministic interpretation and record it.
            override = f"LLM output ({ci.claim_type}, supported={ci.supported}) overridden: claim names {st.mode} surface terms."
            ci, by = planner.interpret_claim_rules(st.mode, st.claim), "rule"
        elif ci.supported and ci.claim_type not in valid:
            ci, by = planner.interpret_claim_rules(st.mode, st.claim), "rule"
        step.decided_by = by
        d = ci.model_dump()
        if override:
            d["override"] = override
        bt = date.fromisoformat(st.temporal_scope["before_target"])
        if ci.referenced_year and ci.referenced_year != bt.year:
            d["reference_gap_note"] = (f"The claim refers to {ci.referenced_year}; the comparison baseline is the first clear "
                                       f"observation near {bt.isoformat()}.")
        d["referenced_date"] = ci.referenced_year
        st.claim_interpretation = d
        if not ci.supported or ci.claim_type == "unsupported":
            self._end(step, f"Claim cannot be tested with satellite imagery: {ci.explanation}", d, ok=False)
            st.status, st.error = "failed", {"code": "UNSUPPORTED_CLAIM", "message": ci.explanation}
            st.current_step = None
            self._save()
            return False
        self._end(step, f"Claim type '{ci.claim_type}': looking for {ci.observable_target} between "
                        f"{st.temporal_scope['before_target']} and {st.temporal_scope['after_target']}.", d)
        return True

    def _execute(self, action: str, reason: str, params: dict, by: str):
        st = self.st
        step = self._begin(action, reason, by, {"parameters": params} if params else None)
        st.tool_call_count += 1
        t0 = time.time()
        if action in ("find_before_observation", "find_after_observation"):
            role = action.split("_")[1]
            summ, det = tools.find_observation(st, role, date.fromisoformat(st.temporal_scope[f"{role}_target"]), 45)
        elif action == "search_more_imagery":
            role = params["role"]
            st.extra_searches += 1
            center = date.fromisoformat(st.temporal_scope[f"{role}_target"]) + timedelta(days=params["shift_days"])
            summ, det = tools.find_observation(st, role, center, params["window_days"])
        elif action in ("analyze_vegetation", "analyze_surface"):
            summ, det = tools.run_analysis(st)
        elif action == "interpret_images":
            summ, det = self._interpret_images()
        elif action == "corroborate_change":
            st.extra_searches += 1
            summ, det = tools.corroborate(st)
            if st.evidence_assessment:
                st.evidence_assessment["stale"] = True
        elif action == "evaluate_evidence":
            summ, det = self._evaluate()
        else:
            raise ValueError(f"Unknown action {action}")
        det["duration_s"] = round(time.time() - t0, 1)
        self._end(step, summ, det)

    def _interpret_images(self):
        st = self.st
        from app.agent.state import asset_dir
        d = asset_dir(st.investigation_id)
        a = st.analysis
        meas = {k: v for k, v in a.items() if isinstance(v, (int, float)) and k.endswith(("_ha", "_pct"))}
        meas["regions"] = [{k: r.get(k) for k in ("id", "area_ha", "spectral_pattern", "indicators") if k in r}
                           for r in a["regions"][:8]]
        s = get_settings()
        key = cache.key("vlm", {"model": s.vlm_model, "mode": st.mode, "claim": st.claim,
                                "scenes": [st.selected_scenes[r]["scene"]["scene_id"] for r in ("before", "after")],
                                "grid": st.methodology["grid"], "meas": meas})
        hit = cache.get_json(key)
        if hit and hit.get("status") == "ok":
            res = hit
            res["cached"] = True
        else:
            try:
                from app.vlm.client import interpret_images
                r = interpret_images(d / "before.png", d / "after.png", d / "overlay.png", mode=st.mode, claim=st.claim,
                                     before_date=st.imagery["before"]["date"], after_date=st.imagery["after"]["date"],
                                     measurements=meas)
                res = r.model_dump(mode="json")
            except Exception as e:  # noqa: BLE001 - VLM failure must not invalidate measurements
                res = {"status": "unavailable", "model": s.vlm_model, "analysis": None, "error": f"{type(e).__name__}: {e}"}
            if res.get("status") == "ok":
                cache.put_json(key, res)
        st.visual_analysis = res
        if st.evidence_assessment:
            st.evidence_assessment["stale"] = True
        if res.get("status") != "ok":
            return f"Visual interpretation unavailable: {res.get('error')}. Continuing with measurements only.", {"status": "unavailable"}
        an = res["analysis"]
        return (f"{res['model']}: {an['summary']} (consistency with measurements: {an['changes_consistent_with_measurements']})",
                {"observations": len(an["observations"]), "cached": res.get("cached", False)})

    def _evaluate(self):
        st = self.st
        a = evaluator.assess(st.mode, st.analysis, st.corroboration, st.visual_analysis)
        st.evidence_assessment = a.model_dump()
        txt = (f"Change level: {a.change_level} ({a.key_metric} = {a.key_value_ha:.1f} ha). "
               f"Data sufficient: {'yes' if a.data_sufficient else 'no'}. "
               f"Corroborated: {a.corroborated if a.corroborated is not None else 'not tested/unclear'}. "
               f"VLM agrees: {a.vlm_agrees if a.vlm_agrees is not None else 'n/a'}. "
               + ("More evidence would help." if a.needs_more_evidence else "Evidence is sufficient to conclude."))
        return txt, st.evidence_assessment

    def _finalize(self, by: str, reason: str, budget_hit: bool = False):
        st = self.st
        step = self._begin("finalize", reason, by)
        a = evaluator.assess(st.mode, st.analysis, st.corroboration, st.visual_analysis)
        st.evidence_assessment = a.model_dump()
        v = evaluator.verdict(st.mode, st.claim_interpretation["claim_type"], st.analysis, a, st.visual_analysis)
        sec = report.build_sections(st)
        if st.analysis is None:
            missing = [r for r in ("before", "after") if r not in st.selected_scenes]
            v["summary"] = ("Inconclusive: no sufficiently clear " + " or ".join(missing) +
                            " observation was found after " + str(sum(st.search_attempts.values())) + " searches.")
            sec["limitations"].append("Insufficient clear imagery for the requested periods.")
        if budget_hit:
            sec["limitations"].append(f"The investigation stopped at its {self.max_steps}-action budget.")
        v.update({"observed": sec["observed"], "inferred": sec["inferred"], "not_established": sec["not_established"]})
        st.verdict = v
        st.limitations = sec["limitations"]
        if st.analysis:
            st.measurements = {k: v_ for k, v_ in st.analysis.items() if k != "stretch"}
            key = "probable_clearing_ha" if st.mode == "forest" else "candidate_changed_area_ha"
            st.headline = {"label": "Probable forest clearing" if st.mode == "forest" else "Candidate surface disturbance",
                           "value": st.analysis[key], "unit": "ha"}
        st.sources = [{"type": "satellite", "provider": "Element 84 Earth Search", "role": role,
                       "collection": x["scene"]["collection"], "scene_id": x["scene"]["scene_id"],
                       "acquired_at": x["scene"]["acquired_at"], "url": x["scene"].get("self_href"),
                       "processing_baseline": x["scene"].get("processing_baseline")}
                      for role, x in st.selected_scenes.items()]
        st.methodology["verification_rules"] = evaluator.__doc__.strip()
        st.methodology["evidence_thresholds"] = evaluator.EvidenceThresholds().model_dump()
        st.progress_percent = 100
        self._end(step, f"Verdict: {v['status']} ({v['confidence']} confidence). {v['summary']}",
                  {"assessment": st.evidence_assessment})
