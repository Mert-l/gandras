"""Bounded investigation loop. Every timeline step corresponds to an executed action."""
from __future__ import annotations

import logging
import time
import traceback
from datetime import date, timedelta

from app import database
from app.agent import planner
from app.agent.state import InvestigationState, Step
from app.config import get_settings
from app.database import now
from app.satellite import cache
from app.verification import report

log = logging.getLogger(__name__)

PROGRESS = {"interpret_claim": 8, "find_before_observation": 22, "find_after_observation": 36,
            "search_more_imagery": None, "interpret_images": 72,
            "corroborate_change": 85, "evaluate_evidence": 90, "finalize": 100}



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
        p = 55 if step.action.startswith("analyze_") else PROGRESS.get(step.action)
        if p:
            self.st.progress_percent = max(self.st.progress_percent, p)
        self._save()

    # ------------------------------------------------------------ main loop
    def run(self) -> InvestigationState:
        from app.llm import registry
        try:
            profile = registry.get_profile((self.st.model or {}).get("id"))
        except KeyError:
            # The chosen model was removed from config since creation: say so, use the default.
            profile = registry.active()
            self.st.errors.append(f"Model {(self.st.model or {}).get('id')} is no longer configured; used {profile.label}.")
        self.st.model = profile.info()
        self.vision_profile = profile
        vid = (self.st.vision_model or {}).get("id")
        if vid and vid != profile.id:
            try:
                self.vision_profile = registry.get_profile(vid)
            except KeyError:
                self.st.errors.append(f"Vision model {vid} is no longer configured; used {profile.label}.")
        self.st.vision_model = self.vision_profile.info()
        with registry.use(profile):
            return self._run()

    def _run(self) -> InvestigationState:
        st = self.st
        st.status = "running"
        stats0 = dict(cache.STATS)
        self._save()
        try:
            if not st.location:
                from app.agent.location import resolve_location
                if not resolve_location(st, self._save, self.use_llm):
                    return st
            from app.agent import tools
            if not self._interpret_claim():
                return st
            self._skip_vision_if_unsupported()
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
        except Exception as e:  # noqa: BLE001 - execution failure, reported as such
            log.error("investigation %s failed: %s", st.investigation_id, traceback.format_exc())
            msg = str(e)
            code = getattr(e, "code", None) or ("SATELLITE_DOWNLOAD_FAILED" if ("STAC" in msg or "Failed to read" in msg) else "ANALYSIS_FAILED")
            self._fail(code, f"{type(e).__name__}: {msg[:300]}")
        st.current_step = None
        self._save()
        return st

    def _skip_vision_if_unsupported(self):
        """A text-only model cannot interpret imagery: record that once, up front, instead of
        spending an agent step on an attempt that can only fail."""
        st, m = self.st, self.st.vision_model or self.st.model or {}
        if m.get("vision", True) or st.visual_analysis is not None:
            return
        msg = f"{m.get('label', 'The selected model')} cannot read images"
        st.visual_analysis = {"status": "unavailable", "model": m.get("label"), "analysis": None,
                              "error": msg + "; visual interpretation skipped, verdict uses measurements only."}
        step = self._begin("skip_visual_interpretation", "The selected model has no image input.", "system")
        self._end(step, msg + ". Visual interpretation is skipped; the verdict will use measurements only.")

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
        from app.agent import claims
        from app.indicators import get
        st = self.st
        step = self._begin("interpret_claim", "Every investigation starts by reading what the claim asserts.", "system")
        reading, by, unsupported = claims.interpret(st.claim, st.requested_indicator, self.use_llm)
        step.decided_by = by
        if reading is None:
            self._end(step, f"Claim cannot be tested with satellite imagery: {unsupported}", {"unsupported": unsupported}, ok=False)
            st.status, st.error = "failed", {"code": "UNSUPPORTED_CLAIM", "message": unsupported}
            st.current_step = None
            self._save()
            return False
        ind = get(reading.indicator)
        st.indicator, st.mode, st.indicator_info = ind.id, ind.id, ind.info()
        d = reading.model_dump()
        bt = date.fromisoformat(st.temporal_scope["before_target"])
        if reading.referenced_year and reading.referenced_year != bt.year:
            d["reference_gap_note"] = (f"The claim refers to {reading.referenced_year}; the comparison baseline is the first clear "
                                       f"observation near {bt.isoformat()}.")
        st.claim_interpretation = d
        how = "chosen by you" if st.requested_indicator != "auto" else "auto-detected"
        asserts = "that no" if reading.asserts == "no_change" else "that"
        dir_txt = f" ({reading.direction})" if reading.direction != "any" else ""
        summary = (f"Indicator: {ind.name} ({how}). The claim asserts {asserts} {ind.evidence_label}{dir_txt} occurred "
                   f"between {st.temporal_scope['before_target']} and {st.temporal_scope['after_target']}.")
        if ind.is_fallback:
            summary += " No specific indicator matched; using the general surface-change fallback (verdict limited)."
        if reading.override:
            summary += f" Note: {reading.override}"
        self._end(step, summary, d)
        return True

    def _execute(self, action: str, reason: str, params: dict, by: str):
        from app.agent import tools
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
        elif action.startswith("analyze_"):
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
        from app.indicators import get
        ind = get(st.indicator)
        key = cache.key("vlm", {"model": (st.vision_model or st.model or {}).get("id", s.vlm_model), "indicator": ind.id, "claim": st.claim,
                                "scenes": [st.selected_scenes[r]["scene"]["scene_id"] for r in ("before", "after")],
                                "grid": st.methodology["grid"], "meas": meas})
        hit = cache.get_json(key)
        if hit and hit.get("status") == "ok":
            res = hit
            res["cached"] = True
        else:
            try:
                from app.vlm.client import interpret_images
                from app.llm import registry as _reg
                with _reg.use(getattr(self, "vision_profile", None) or _reg.active()):
                    r = interpret_images(d / "before.png", d / "after.png", d / "overlay.png", mode=ind.id, guidance=ind.vlm_guidance, claim=st.claim,
                                         before_date=st.imagery["before"]["date"], after_date=st.imagery["after"]["date"],
                                         measurements=meas)
                res = r.model_dump(mode="json")
            except Exception as e:  # noqa: BLE001 - VLM failure must not invalidate measurements
                res = {"status": "unavailable", "model": (st.vision_model or st.model or {}).get("label", s.vlm_model), "analysis": None,
                       "error": f"{type(e).__name__}: {e}"}
            if res.get("status") == "ok":
                cache.put_json(key, res)
        st.visual_analysis = res
        if st.evidence_assessment:
            st.evidence_assessment["stale"] = True
        if res.get("status") != "ok":
            return f"Visual interpretation unavailable: {res.get('error')}. Continuing with measurements only.", {"status": "unavailable"}
        an = res["analysis"]
        prefix = "(cached interpretation from an earlier run on the same scenes) " if res.get("cached") else ""
        return (f"{prefix}{res['model']}: {an['summary']} (consistency with measurements: {an['changes_consistent_with_measurements']})",
                {"observations": len(an["observations"]), "cached": res.get("cached", False)})

    def _assess(self):
        from app.verification import evaluator
        from app.indicators import get
        st = self.st
        return evaluator.assess(get(st.indicator), st.claim_interpretation["direction"], st.analysis,
                                st.corroboration, st.visual_analysis)

    def _evaluate(self):
        st = self.st
        a = self._assess()
        st.evidence_assessment = a.model_dump()
        txt = (f"Change level: {a.change_level} ({a.key_metric} = {a.key_value_ha:.1f} ha). "
               f"Data sufficient: {'yes' if a.data_sufficient else 'no'}. "
               f"Corroborated: {a.corroborated if a.corroborated is not None else 'not tested/unclear'}. "
               f"VLM agrees: {a.vlm_agrees if a.vlm_agrees is not None else 'n/a'}. "
               + ("More evidence would help." if a.needs_more_evidence else "Evidence is sufficient to conclude."))
        return txt, st.evidence_assessment

    def _open_source_context(self, provisional: dict):
        """Weak or imagery-starved findings: have the planning model look for non-imagery evidence."""
        from app.agent import osint
        from app.location.scope import constrain_verdict
        st = self.st
        if st.osint is not None or not self.use_llm:
            return
        check = constrain_verdict(provisional, st.location, st.location_resolution) if st.location_resolution else provisional
        reason = osint.trigger_reason(check, st.analysis)
        if reason is None:
            return
        parent = self._begin("research_open_sources", f"Satellite evidence is weak ({reason}); looking for non-imagery "
                             "sources about the claim.", "rule")
        active: list = []

        def notify(action, summary, details):
            if active:
                self._end(active.pop(), active_summary.pop())
            active.append(self._begin(action, "The planning model searches and reads open sources.", "llm", details))
            active_summary.append(summary)
        active_summary: list = []
        loc = st.location or {}
        period = f"{(st.temporal_scope or {}).get('before_target')} to {(st.temporal_scope or {}).get('after_target')}"
        from app.indicators import get as _ind
        st.osint = osint.research(st.claim, loc.get("name") or "", period, reason, notify,
                                  topic_terms=list(_ind(st.indicator).keywords) if st.indicator else None)
        if active:
            self._end(active.pop(), active_summary.pop())
        o, c = st.osint, st.osint.get("counts", {})
        summary = ({"findings": f"{len(o['findings'])} verified statement(s) from {len(o['sources'])} source(s): "
                                f"{c.get('supports_claim', 0)} support, {c.get('contradicts_claim', 0)} contradict, "
                                f"{c.get('context', 0)} context. Context only; the satellite verdict is unchanged.",
                    "no_findings": f"{o['summary']} ({len(o['sources'])} source(s) read.)",
                    "unavailable": f"Open-source research unavailable: {o['summary']}"}[o["status"]])
        self._end(parent, summary, {"status": o["status"], "queries": o["queries"], "sources": len(o["sources"])},
                  ok=o["status"] != "unavailable")

    def _finalize(self, by: str, reason: str, budget_hit: bool = False):
        from app.verification import evaluator
        st = self.st
        from app.indicators import get
        ind = get(st.indicator)
        ci = st.claim_interpretation
        a = self._assess()
        st.evidence_assessment = a.model_dump()
        v = evaluator.verdict(ind, ci["asserts"], ci["direction"], st.analysis, a, st.visual_analysis)
        self._open_source_context(v)
        step = self._begin("finalize", reason, by)
        sec = report.build_sections(st)
        if st.analysis is None:
            missing = [r for r in ("before", "after") if r not in st.selected_scenes]
            v["summary"] = ("Inconclusive: no sufficiently clear " + " or ".join(missing) +
                            " observation was found after " + str(sum(st.search_attempts.values())) + " searches.")
            sec["limitations"].append("Insufficient clear imagery for the requested periods.")
        if budget_hit:
            sec["limitations"].append(f"The investigation stopped at its {self.max_steps}-action budget.")
        if st.location_resolution:
            sec["limitations"].extend(st.location_resolution.get("location", {}).get("uncertainty", []) if st.location_resolution.get("location") else [])
            if (st.temporal_scope or {}).get("date_basis"):
                sec["limitations"].append("Automatically selected dates are comparison targets inferred from the claim; acquisition timestamps identify the actual observations.")
            from app.location.scope import constrain_verdict
            v = constrain_verdict(v, st.location, st.location_resolution)
        o = st.osint or {}
        if o.get("status") == "findings":
            c = o.get("counts", {})
            sec["inferred"].append(f"Open-source context (not satellite evidence): {c.get('supports_claim', 0)} statement(s) "
                                   f"support the claim, {c.get('contradicts_claim', 0)} contradict it, {c.get('context', 0)} "
                                   f"give context. {o.get('summary', '')}".strip())
        if o:
            sec["limitations"].append(o["note"])
        v.update({"observed": sec["observed"], "inferred": sec["inferred"], "not_established": sec["not_established"]})
        st.verdict = v
        st.limitations = sec["limitations"]
        if st.analysis:
            st.measurements = {k: v_ for k, v_ in st.analysis.items() if k != "stretch"}
            st.headline = {"label": ind.evidence_label.capitalize(), "value": st.analysis["evidence_ha"], "unit": "ha"}
        st.sources = [{"type": "satellite", "provider": "Element 84 Earth Search", "role": role,
                       "collection": x["scene"]["collection"], "scene_id": x["scene"]["scene_id"],
                       "acquired_at": x["scene"]["acquired_at"], "url": x["scene"].get("self_href"),
                       "processing_baseline": x["scene"].get("processing_baseline")}
                      for role, x in st.selected_scenes.items()]
        if st.osint:
            st.sources.extend({"type": "open_source", **s} for s in st.osint.get("sources", []))
        if st.location_resolution:
            st.sources.extend({"type": "geographic", **s} for s in st.location_resolution.get("sources", []))
            st.methodology["location_discovery"] = {k: st.location_resolution.get(k) for k in ("verification_status", "explanation", "attempts")}
            st.methodology["aoi_boundary_status"] = (st.location or {}).get("boundary_status")
        st.methodology["verification_rules"] = evaluator.__doc__.strip()
        st.methodology["evidence_thresholds"] = evaluator.EvidenceThresholds().model_dump()
        st.progress_percent = 100
        self._end(step, f"Verdict: {v['status']} ({v['confidence']} confidence). {v['summary']}",
                  {"assessment": st.evidence_assessment})
