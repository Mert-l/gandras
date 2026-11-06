"""Which language/vision models the app may use, and which one the current investigation uses.

Sources of models:
- every model installed in the configured Ollama (discovered live; vision from Ollama's capabilities);
- optional entries in MODELS_FILE (default config/models.json) for remote Ollama hosts,
  OpenAI-compatible servers (vLLM, LM Studio, llama.cpp, OpenAI, OpenRouter...) and Anthropic.

The browser only ever sends a model *id*; base URLs and API-key variable names come from server
config, so a request cannot point the server at an arbitrary endpoint. API keys are read from the
environment variable named in `api_key_env` and never stored in the file or returned by the API.
"""
from __future__ import annotations

import contextlib
import contextvars
import json
import logging
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

import httpx

from app.config import get_settings

log = logging.getLogger(__name__)

Provider = Literal["ollama", "openai", "anthropic"]


@dataclass
class ModelProfile:
    id: str
    label: str
    provider: str
    model: str
    base_url: str = ""
    api_key_env: str = ""
    location: str = "local"          # local | on_premise | cloud
    vision: bool = False
    note: str = ""
    max_tokens: int = 3000
    available: bool = False
    status: str = "unknown"
    extra: dict = field(default_factory=dict)

    def api_key(self) -> str:
        return os.environ.get(self.api_key_env, "") if self.api_key_env else ""

    def public(self) -> dict:
        return {k: getattr(self, k) for k in ("id", "label", "provider", "model", "location", "vision",
                                               "available", "status", "note")}

    def info(self) -> dict:
        return {k: getattr(self, k) for k in ("id", "label", "provider", "model", "location", "vision")}


_current: contextvars.ContextVar[ModelProfile | None] = contextvars.ContextVar("current_model", default=None)
_cache: dict = {"at": 0.0, "profiles": None}
_lock = threading.Lock()
CACHE_S = 20.0


def _label(name: str) -> str:
    base, _, tag = name.partition(":")
    pretty = base.replace("-", " ").replace("_", " ").title().replace("Qwen3.5", "Qwen 3.5")
    return f"{pretty} {tag.upper()}" if tag and tag != "latest" else pretty


def _ollama_profiles(base_url: str, location: str, prefix: str = "ollama") -> list[ModelProfile]:
    try:
        r = httpx.get(base_url.rstrip("/") + "/api/tags", timeout=2.5)
        r.raise_for_status()
        models = r.json().get("models", [])
    except Exception as e:  # noqa: BLE001 - unreachable Ollama just yields no discovered models
        log.info("Ollama at %s unreachable: %s", base_url, type(e).__name__)
        return []
    out = []
    for m in models:
        caps = m.get("capabilities") or []
        if caps and "completion" not in caps:      # embedding-only models cannot chat
            continue
        name = m.get("name") or m.get("model")
        size = (m.get("details") or {}).get("parameter_size")
        out.append(ModelProfile(
            id=f"{prefix}:{name}", label=_label(name), provider="ollama", model=name, base_url=base_url,
            location=location, vision="vision" in caps, available=True, status="ready",
            note=(f"{size} parameters" if size else "")))
    return out


def _configured_profiles() -> list[ModelProfile]:
    s = get_settings()
    path = s.resolve(Path(s.models_file))
    if not path.is_file():
        return []
    try:
        entries = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        log.warning("Ignoring unreadable %s: %s", path, e)
        return []
    out = []
    for e in entries if isinstance(entries, list) else []:
        if isinstance(e, dict) and set(e) <= {"_comment"}:
            continue
        try:
            provider = e["provider"]
            if provider not in ("ollama", "openai", "anthropic"):
                raise ValueError(f"unknown provider {provider}")
            if provider == "ollama":
                # A whole remote Ollama host: discover its models.
                out += _ollama_profiles(e["base_url"], e.get("location", "on_premise"), prefix=e.get("id", "ollama-remote"))
                continue
            p = ModelProfile(id=e["id"], label=e.get("label", e["model"]), provider=provider, model=e["model"],
                             base_url=e.get("base_url", "https://api.anthropic.com" if provider == "anthropic" else ""),
                             api_key_env=e.get("api_key_env", ""), location=e.get("location", "cloud"),
                             vision=bool(e.get("vision", False)), note=e.get("note", ""),
                             max_tokens=int(e.get("max_tokens", 3000)))
            if not p.base_url:
                raise ValueError("base_url is required for openai providers")
            out.append(p)
        except (KeyError, TypeError, ValueError) as err:
            log.warning("Ignoring invalid model entry %r: %s", e, err)
    return out


def _check(p: ModelProfile) -> None:
    """Cheap availability check; never sends a prompt (no cost)."""
    if p.provider == "ollama":
        return
    if p.api_key_env and not p.api_key():
        p.available, p.status = False, f"API key missing ({p.api_key_env})"
        return
    if p.provider == "anthropic":
        p.available, p.status = True, "configured"
        return
    try:
        headers = {"Authorization": f"Bearer {p.api_key()}"} if p.api_key() else {}
        r = httpx.get(p.base_url.rstrip("/") + "/models", headers=headers, timeout=2.5)
        p.available = r.status_code < 400
        p.status = "ready" if p.available else f"unreachable (HTTP {r.status_code})"
    except Exception as e:  # noqa: BLE001
        p.available, p.status = False, f"unreachable ({type(e).__name__})"


def list_profiles(refresh: bool = False) -> list[ModelProfile]:
    with _lock:
        if not refresh and _cache["profiles"] is not None and time.monotonic() - _cache["at"] < CACHE_S:
            return _cache["profiles"]
        s = get_settings()
        profiles = _ollama_profiles(s.ollama_url, "local") + _configured_profiles()
        for p in profiles:
            _check(p)
        default_id = default_model_id(profiles)
        if not any(p.id == default_id for p in profiles):
            # Keep the configured default visible even when Ollama is down or it isn't installed.
            profiles.insert(0, ModelProfile(id=default_id, label=_label(s.vlm_model), provider="ollama",
                                            model=s.vlm_model, base_url=s.ollama_url, available=False,
                                            status="not installed or Ollama unreachable"))
        seen, unique = set(), []
        for p in profiles:
            if p.id not in seen:
                seen.add(p.id)
                unique.append(p)
        _cache.update(at=time.monotonic(), profiles=unique)
        return unique


def default_model_id(profiles: list[ModelProfile] | None = None) -> str:
    s = get_settings()
    return s.default_model or f"ollama:{s.vlm_model}"


def get_profile(model_id: str | None) -> ModelProfile:
    """Resolve an id from the registry. Unknown ids raise KeyError (callers validate requests)."""
    wanted = model_id or default_model_id()
    for p in list_profiles():
        if p.id == wanted:
            return p
    raise KeyError(wanted)


def active() -> ModelProfile:
    """The model for the current investigation (context), else the configured default."""
    p = _current.get()
    if p is not None:
        return p
    try:
        return get_profile(None)
    except KeyError:
        s = get_settings()
        return ModelProfile(id=default_model_id(), label=_label(s.vlm_model), provider="ollama",
                            model=s.vlm_model, base_url=s.ollama_url)


@contextlib.contextmanager
def use(profile: ModelProfile):
    token = _current.set(profile)
    try:
        yield profile
    finally:
        _current.reset(token)


def as_dict(p: ModelProfile) -> dict:
    return asdict(p)
