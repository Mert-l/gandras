"""Indicator registry."""
from app.indicators.base import Indicator
from app.indicators.built_up import BUILT_UP
from app.indicators.burn import BURN
from app.indicators.forest import FOREST
from app.indicators.generic import GENERIC
from app.indicators.surface import SURFACE
from app.indicators.vegetation_cover import VEGETATION
from app.indicators.water import WATER

REGISTRY: dict[str, Indicator] = {i.id: i for i in (FOREST, SURFACE, BUILT_UP, WATER, BURN, VEGETATION, GENERIC)}
FALLBACK_ID = GENERIC.id
LEGACY_MODES = {"forest": FOREST.id, "mining": SURFACE.id}


def get(indicator_id: str) -> Indicator:
    return REGISTRY[LEGACY_MODES.get(indicator_id, indicator_id)]


def public_list() -> list[dict]:
    return [i.public() for i in REGISTRY.values()]
