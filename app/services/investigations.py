"""Create and run investigations in a single background worker."""
from __future__ import annotations

import logging
import re
import threading
from datetime import date
from concurrent.futures import ThreadPoolExecutor

from app import database
from app.agent.state import InvestigationState, Step, load_demo_cases, new_id

log = logging.getLogger(__name__)
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="investigation")
_location_lock = threading.Lock()


class RequestError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def create(indicator: str, claim: str, case_id: str | None, custom: dict | None, options: dict | None) -> InvestigationState:
    if case_id:
        case = next((c for c in load_demo_cases() if c["id"] == case_id), None)
        if case is None:
            raise RequestError("INVALID_LOCATION", f"Unknown case_id '{case_id}'")
        loc = {"name": case["name"], "latitude": case["latitude"], "longitude": case["longitude"], "width_km": case["width_km"]}
        scope = {"before_target": case["before_date"], "after_target": case["after_date"]}
    elif custom:
        loc = {"name": f"Custom AOI ({custom['latitude']:.4f}, {custom['longitude']:.4f})", "latitude": custom["latitude"],
               "longitude": custom["longitude"], "width_km": custom["width_km"]}
        scope = {"before_target": str(custom["before_date"]), "after_target": str(custom["after_date"])}
    else:
        loc = None
        today = date.today()
        years = sorted({int(y) for y in re.findall(r'\b(?:19|20)\d{2}\b', claim) if 2017 <= int(y) <= today.year})
        baseline = date(years[0], 1, 1) if years else date(max(2017, today.year-3), 1, 1)
        end = min(today, date(years[-1], 12, 31)) if len(years) > 1 else today
        scope = {"before_target": baseline.isoformat(), "after_target": end.isoformat(),
                 "date_basis": "Claim years where present; otherwise a three-year baseline and today. Targets are not acquisition dates."}
    st = InvestigationState(investigation_id=new_id(), mode=indicator, requested_indicator=indicator, claim=claim,
                            indicator=None if indicator == "auto" else indicator, case_id=case_id, location=loc,
                            temporal_scope=scope, options=options or {})
    database.save(st.model_dump(mode="json"))
    _executor.submit(_run, st.investigation_id)
    return st


def confirm_location(inv_id: str, candidate_id: str | None, custom: dict | None):
    """Resume only an idle investigation; a running worker cannot be overwritten."""
    with _location_lock:
        raw = database.load(inv_id)
        if raw is None:
            raise RequestError("NOT_FOUND", "No such investigation")
        if raw["status"] in ("queued", "running"):
            raise RequestError("INVESTIGATION_BUSY", "Wait for location research to finish before editing the location.")
        st = InvestigationState.model_validate(raw)
        if custom:
            location = {"name": "User-supplied location", **{k: custom[k] for k in ('latitude', 'longitude', 'width_km')},
                        "boundary_status": "provisional", "verification_status": "user_supplied",
                        "uncertainty": ["User-supplied sampling window; property boundary is not verified."]}
            st.temporal_scope = {"before_target": str(custom['before_date']), "after_target": str(custom['after_date'])}
        else:
            candidates = (st.location_resolution or {}).get('candidates', [])
            location = next((dict(c) for c in candidates if c['id'] == candidate_id), None)
            if location is None:
                raise RequestError("INVALID_CANDIDATE", "Choose a candidate returned by this investigation.")
            location['verification_status'] = 'user_confirmed'
        previous = st.location_resolution or {}
        st.location = location
        st.location_resolution = {**previous, 'status': 'resolved', 'verification_status': location['verification_status'],
                                  'location': location, 'confirmed_at': database.now(), 'explanation': 'Location selected or corrected by the user.'}
        # Satellite products and conclusions cannot carry over to a corrected AOI.
        st.selected_scenes, st.candidates = {}, {}
        st.search_attempts, st.extra_searches = {'before': 0, 'after': 0}, 0
        st.analysis = st.imagery = st.measurements = st.verdict = st.headline = None
        st.visual_analysis = st.corroboration = st.evidence_assessment = None
        st.methodology, st.sources, st.limitations = {}, [], []
        st.claim_interpretation = None
        st.steps.append(Step(index=len(st.steps)+1, action="confirm_location", status="completed",
                             summary="User confirmed or corrected the location; satellite evidence will be recomputed.",
                             decided_by="system", finished_at=database.now()))
        st.tool_call_count, st.progress_percent = 0, 0
        st.status, st.error, st.errors, st.current_step = 'queued', None, [], None
        st.case_id, st.from_cache = None, False
        database.save(st.model_dump(mode='json'))
        _executor.submit(_run, inv_id)
        return st


def _run(inv_id: str) -> None:
    raw = database.load(inv_id)
    if raw is None:
        return
    try:
        # Keep the interface and saved reports available when native analysis
        # dependencies cannot load on the host.
        from app.agent.orchestrator import Runner

        Runner(InvestigationState.model_validate(raw)).run()
    except Exception:  # noqa: BLE001 - last-resort guard; Runner already records failures
        log.exception("runner crashed")
        current = database.load(inv_id) or raw
        if current.get("status") not in ("completed", "failed", "interrupted"):
            current.update(status="failed", current_step=None,
                           error={"code": "ANALYSIS_FAILED", "message":
                                  "The analysis runtime could not start. Check the local server logs for dependency errors."})
            database.save(current)
