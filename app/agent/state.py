"""Investigation state. Persisted as JSON after every step; arrays live in the cache/asset dirs."""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.config import PROJECT_ROOT
from app.database import now

DEMO_CASES_PATH = PROJECT_ROOT / "app" / "demo_cases.json"
ASSETS_ROOT = PROJECT_ROOT / "data" / "investigations"

Mode = Literal["mining", "forest"]


def load_demo_cases() -> list[dict]:
    return json.loads(DEMO_CASES_PATH.read_text())


def new_id() -> str:
    return "inv_" + uuid.uuid4().hex[:10]


def asset_dir(inv_id: str) -> Path:
    d = ASSETS_ROOT / inv_id
    d.mkdir(parents=True, exist_ok=True)
    return d


class Step(BaseModel):
    index: int
    action: str
    status: Literal["running", "completed", "failed"] = "running"
    summary: str = ""
    reason: str | None = None
    decided_by: Literal["llm", "rule", "system"] = "system"
    started_at: str = Field(default_factory=now)
    finished_at: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class InvestigationState(BaseModel):
    investigation_id: str
    mode: Mode
    claim: str
    case_id: str | None = None
    status: Literal["queued", "running", "completed", "failed", "interrupted"] = "queued"
    created_at: str = Field(default_factory=now)
    updated_at: str = Field(default_factory=now)
    progress_percent: int = 0
    current_step: str | None = None
    options: dict[str, Any] = Field(default_factory=dict)

    location: dict | None = None            # name, latitude, longitude, width_km
    temporal_scope: dict | None = None      # before_target, after_target
    claim_interpretation: dict | None = None

    candidates: dict[str, list[dict]] = Field(default_factory=dict)   # role -> ranked scene summaries
    selected_scenes: dict[str, dict] = Field(default_factory=dict)    # role -> scene meta + quality
    search_attempts: dict[str, int] = Field(default_factory=lambda: {"before": 0, "after": 0})
    extra_searches: int = 0
    analysis: dict | None = None
    corroboration: dict | None = None
    visual_analysis: dict | None = None
    evidence_assessment: dict | None = None

    imagery: dict | None = None
    measurements: dict | None = None
    verdict: dict | None = None
    headline: dict | None = None
    limitations: list[str] = Field(default_factory=list)
    methodology: dict = Field(default_factory=dict)
    sources: list[dict] = Field(default_factory=list)
    steps: list[Step] = Field(default_factory=list)
    tool_call_count: int = 0
    from_cache: bool = False
    errors: list[str] = Field(default_factory=list)
    error: dict | None = None

    def public(self) -> dict:
        """API view (internal working fields are kept but large ones trimmed)."""
        d = self.model_dump()
        for k in ("candidates",):
            d.pop(k, None)
        return d
