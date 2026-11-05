"""Create and run investigations in a single background worker."""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

from app import database
from app.agent.orchestrator import Runner
from app.agent.state import InvestigationState, load_demo_cases, new_id

log = logging.getLogger(__name__)
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="investigation")


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
        raise RequestError("INVALID_LOCATION", "Provide a case_id or a custom location")
    st = InvestigationState(investigation_id=new_id(), mode=indicator, requested_indicator=indicator, claim=claim,
                            indicator=None if indicator == "auto" else indicator, case_id=case_id, location=loc,
                            temporal_scope=scope, options=options or {})
    database.save(st.model_dump(mode="json"))
    _executor.submit(_run, st.investigation_id)
    return st


def _run(inv_id: str) -> None:
    raw = database.load(inv_id)
    if raw is None:
        return
    try:
        Runner(InvestigationState.model_validate(raw)).run()
    except Exception:  # noqa: BLE001 - last-resort guard; Runner already records failures
        log.exception("runner crashed")
