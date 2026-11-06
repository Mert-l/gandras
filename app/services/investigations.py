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


def _on(year: int, today: date) -> date:
    """Today's calendar date in `year` (29 Feb falls back to 28 Feb)."""
    return date(year, today.month, min(today.day, 28 if today.month == 2 else today.day))


def automatic_dates(claim: str, today: date) -> dict:
    """Comparison targets for claims without explicit dates, anchored to one calendar date.

    Both targets fall on today's month and day (in the first claim year, and in the last claim year
    or today), so the two observations come from the same season: a January-vs-October pair mixes
    snow, sun angle and vegetation phase into the change signal. If the claim names only the
    current year, no same-season baseline exists, so the start of that year is used and flagged.
    """
    years = sorted({int(y) for y in re.findall(r'\b(?:19|20)\d{2}\b', claim) if 2017 <= int(y) <= today.year})
    first = years[0] if years else max(2017, today.year - 3)
    after = _on(years[-1], today) if len(years) > 1 and years[-1] < today.year else today
    before = _on(first, today)
    basis = ("Claim years where present, otherwise a three-year baseline; both targets use today's calendar date "
             "so the observations share a season. Changes earlier in the first claim year may fall before the baseline. "
             "Targets are not acquisition dates.")
    if (after - before).days < 60:
        before = date(first, 1, 1)
        basis = ("The claim names only the current year, so the baseline is 1 January and the two observations "
                 "are from different seasons. Targets are not acquisition dates.")
    return {"before_target": before.isoformat(), "after_target": after.isoformat(), "date_basis": basis}


def resolve_model(model_id: str | None) -> dict:
    """Validate a requested model id against the registry and return its public info."""
    from app.llm import registry
    try:
        p = registry.get_profile(model_id)
    except KeyError:
        raise RequestError("UNKNOWN_MODEL", f"Model '{model_id}' is not configured. See GET /models.") from None
    if model_id and not p.available:
        raise RequestError("MODEL_UNAVAILABLE", f"{p.label} is not available: {p.status}.")
    return p.info()


def create(indicator: str, claim: str, case_id: str | None, custom: dict | None, options: dict | None) -> InvestigationState:
    model = resolve_model((options or {}).get("model"))
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
        scope = automatic_dates(claim, date.today())
    st = InvestigationState(investigation_id=new_id(), mode=indicator, requested_indicator=indicator, claim=claim,
                            indicator=None if indicator == "auto" else indicator, case_id=case_id, location=loc,
                            temporal_scope=scope, options=options or {}, model=model)
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
                                  'location': location, 'selection': 'user', 'confirmed_at': database.now(),
                                  'explanation': 'Location selected or corrected by the user.'}
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
