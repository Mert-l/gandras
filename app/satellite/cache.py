"""Deterministic on-disk cache keyed by the parameters that produced each entry."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np

from app.config import get_settings

PROCESSING_VERSION = "p2-v1"


def key(kind: str, params: dict) -> str:
    blob = json.dumps({"kind": kind, "v": PROCESSING_VERSION, **params}, sort_keys=True, default=str)
    return f"{kind}_{hashlib.sha1(blob.encode()).hexdigest()[:16]}"


def _dir() -> Path:
    s = get_settings()
    d = s.resolve(s.cache_dir)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _enabled() -> bool:
    return get_settings().enable_cache


def get_json(k: str) -> dict | None:
    p = _dir() / f"{k}.json"
    if not _enabled() or not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError:
        return None


def put_json(k: str, data: dict) -> None:
    p = _dir() / f"{k}.json"
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1, default=str))
    tmp.replace(p)


def get_arrays(k: str, expected: list[str], shape: tuple[int, int]) -> dict[str, np.ndarray] | None:
    """Return cached arrays only if every expected array is present with the right shape."""
    p = _dir() / f"{k}.npz"
    if not _enabled() or not p.exists():
        return None
    try:
        with np.load(p) as z:
            if not all(n in z.files and z[n].shape == shape for n in expected):
                return None
            return {n: z[n] for n in z.files}
    except Exception:  # noqa: BLE001 - corrupt cache is treated as a miss
        return None


def put_arrays(k: str, arrays: dict[str, np.ndarray]) -> None:
    p = _dir() / f"{k}.npz"
    tmp = _dir() / f"{k}.tmp.npz"
    np.savez_compressed(tmp, **arrays)
    tmp.replace(p)
