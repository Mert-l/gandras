from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, Field


class Clues(BaseModel):
    organizations: list[str] = Field(default_factory=list, max_length=8)
    projects: list[str] = Field(default_factory=list, max_length=8)
    places: list[str] = Field(default_factory=list, max_length=8)
    countries: list[str] = Field(default_factory=list, max_length=4)
    queries: list[str] = Field(default_factory=list, max_length=4)


class Citation(BaseModel):
    source_id: int
    quote: str = Field(min_length=8, max_length=600)
    supports: list[Literal['project', 'operator', 'country', 'region', 'location', 'area', 'boundary']]
    source_quality: Literal['official', 'reputable', 'unknown'] = 'unknown'


class Proposal(BaseModel):
    name: str = Field(max_length=200)
    project: str = ''
    operator: str = ''
    country: str
    country_code: str = Field(pattern=r'^[a-zA-Z]{2}$')
    region: str = ''
    geocode_query: str = Field(max_length=300)
    specific_site: bool = False
    identity_consistent: bool = False
    conflicts: list[str] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list, max_length=12)
    area_ha: float | None = Field(default=None, gt=0)
    # Only a fetched source or a link actually found on that source can supply geometry.
    boundary_url: str | None = None
    boundary_is_project: bool = False
    explanation: str = Field(default='', max_length=800)


class Research(BaseModel):
    candidates: list[Proposal] = Field(default_factory=list, max_length=4)
    next_queries: list[str] = Field(default_factory=list, max_length=3)
    explanation: str = Field(default='', max_length=800)
