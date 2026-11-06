"""Structured (schema-constrained) chat and image interpretation for the active model.

The model comes from app.llm.registry: the investigation's chosen model (context) or the default.
Ollama is called through `_client` (kept for tests); other providers via app.llm.backends.

The VLM only interprets images. All measurements come from the geospatial pipeline and are
passed in as text; nothing here computes or invents numbers.
"""
from __future__ import annotations

import logging
import re
import tempfile
import time
from pathlib import Path
from typing import Any, Literal

import httpx
import ollama
from PIL import Image
from pydantic import BaseModel, ValidationError

from app.config import get_settings
from app.vlm.prompts import SYSTEM_PROMPT, build_user_prompt
from app.vlm.schemas import VisualAnalysis, VLMResult

log = logging.getLogger(__name__)

MAX_IMAGE_SIDE = 1024
IMAGE_TIMEOUT_FLOOR_S = 180.0
NUM_CTX = 8192
NUM_PREDICT = 3000
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


class VLMError(RuntimeError):
    def __init__(self, message: str, attempts: int = 0):
        super().__init__(message)
        self.attempts = attempts


def _client(timeout_s: float) -> ollama.Client:
    """Factory for the configured local Ollama (monkeypatched in tests)."""
    return ollama.Client(host=get_settings().ollama_url, timeout=timeout_s)


def _ollama_client(profile, timeout_s: float) -> ollama.Client:
    if not profile.base_url or profile.base_url.rstrip("/") == get_settings().ollama_url.rstrip("/"):
        return _client(timeout_s)
    return ollama.Client(host=profile.base_url, timeout=timeout_s)


def model_available() -> bool:
    """True if the default model is installed/configured and reachable."""
    from app.llm import registry
    try:
        return registry.get_profile(None).available
    except KeyError:
        return False


def _call(profile, messages: list[dict], schema: type[BaseModel], images: list[Path] | None,
          temperature: float, timeout_s: float) -> tuple[str, bool]:
    """One request to the active provider -> (raw text, truncated)."""
    from app.llm import backends
    fmt = schema.model_json_schema()
    if profile.provider == "ollama":
        msgs = [dict(m) for m in messages]
        if images:
            i = next(i for i, m in enumerate(msgs) if m["role"] == "user")
            msgs[i]["images"] = [str(p) for p in images]
        try:
            resp = _ollama_client(profile, timeout_s).chat(
                model=profile.model, messages=msgs, format=fmt, think=False, stream=False,
                options={"temperature": temperature, "num_ctx": NUM_CTX, "num_predict": NUM_PREDICT})
        except (ConnectionError, httpx.HTTPError, ollama.ResponseError, OSError) as e:
            raise VLMError(f"Ollama request failed ({type(e).__name__}): {e}") from e
        return resp.message.content or "", getattr(resp, "done_reason", None) == "length"
    fn = backends.openai_chat if profile.provider == "openai" else backends.anthropic_chat
    try:
        return fn(profile, messages, fmt, schema.__name__, images, temperature, timeout_s)
    except backends.BackendError as e:
        raise VLMError(str(e)) from e


def _clean(content: str) -> str:
    content = _THINK_RE.sub("", content or "").strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content).strip()
    return content


def _structured_chat_impl(system: str, user: str, schema: type[BaseModel], images: list[Path] | None,
                          retries: int, temperature: float, timeout_s: float | None) -> tuple[BaseModel, int]:
    from app.llm import registry
    s = get_settings()
    profile = registry.active()
    if images and not profile.vision:
        raise VLMError(f"{profile.label} cannot read images (no vision input)")
    timeout = timeout_s if timeout_s is not None else s.http_timeout_s
    messages: list[dict[str, Any]] = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    last_err = ""
    attempts = 0
    for _ in range(retries + 1):
        attempts += 1
        try:
            raw, truncated = _call(profile, messages, schema, images, temperature, timeout)
        except VLMError as e:
            e.attempts = attempts
            raise
        content = _clean(raw)
        try:
            return schema.model_validate_json(content), attempts
        except ValidationError as e:
            short = "; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors()[:5])
            log.warning("VLM output failed validation (attempt %d): %s", attempts, short)
            messages = messages + [
                {"role": "assistant", "content": content[:2000]},
                {"role": "user", "content": f"That output was invalid ({short}). Reply again with only valid JSON matching the schema."},
            ]
            last_err = ("output truncated at token limit; " if truncated else "") + short
    raise VLMError(f"Model output failed schema validation after {attempts} attempts: {last_err}", attempts)


def structured_chat(system: str, user: str, schema: type[BaseModel], images: list[Path] | None = None,
                    retries: int = 2, temperature: float = 0.1, timeout_s: float | None = None) -> BaseModel:
    """Schema-constrained chat. Retries only on invalid JSON / validation errors; raises VLMError otherwise."""
    obj, _ = _structured_chat_impl(system, user, schema, images, retries, temperature, timeout_s)
    return obj


def _prepare_image(src: Path, tmpdir: Path, idx: int) -> Path:
    src = Path(src)
    with Image.open(src) as im:
        if max(im.size) <= MAX_IMAGE_SIDE:
            return src
        im = im.convert("RGB")
        im.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE), Image.LANCZOS)
        out = tmpdir / f"img{idx}.png"
        im.save(out, "PNG")
        return out


def interpret_images(before_png: Path, after_png: Path, overlay_png: Path, *, mode: str,
                     claim: str, before_date: str, after_date: str, measurements: dict,
                     guidance: str | None = None) -> VLMResult:
    """Ask the VLM to interpret the image triplet. Never raises; never fabricates a result."""
    from app.llm import registry
    s = get_settings()
    model_name = registry.active().label
    t0 = time.monotonic()
    attempts = 0
    try:
        if not guidance and mode not in ("forest", "mining"):
            raise ValueError(f"unknown mode {mode!r} and no guidance given")
        user = build_user_prompt(mode=mode, claim=claim, before_date=before_date, after_date=after_date,
                                 measurements=measurements or {}, guidance=guidance)
        with tempfile.TemporaryDirectory(prefix="vlm_") as td:
            imgs = [_prepare_image(p, Path(td), i) for i, p in enumerate((before_png, after_png, overlay_png))]
            analysis, attempts = _structured_chat_impl(
                SYSTEM_PROMPT, user, VisualAnalysis, imgs, retries=2, temperature=0.1,
                timeout_s=max(s.http_timeout_s, IMAGE_TIMEOUT_FLOOR_S))
        return VLMResult(status="ok", model=model_name, analysis=analysis, attempts=attempts,
                         duration_s=round(time.monotonic() - t0, 2))
    except Exception as e:  # noqa: BLE001 - contract: never raise
        attempts = getattr(e, "attempts", attempts)
        log.warning("VLM interpretation unavailable: %s", e)
        return VLMResult(status="unavailable", model=model_name, error=f"{type(e).__name__}: {e}",
                         attempts=attempts, duration_s=round(time.monotonic() - t0, 2))
