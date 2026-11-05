"""Ollama client for structured (schema-constrained) chat and image interpretation.

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
    """Factory (monkeypatched in tests)."""
    return ollama.Client(host=get_settings().ollama_url, timeout=timeout_s)


def model_available() -> bool:
    """True if Ollama answers /api/tags and lists the configured model."""
    s = get_settings()
    try:
        r = httpx.get(f"{s.ollama_url.rstrip('/')}/api/tags", timeout=2.0)
        r.raise_for_status()
        names = {m.get("name") for m in r.json().get("models", [])} | {m.get("model") for m in r.json().get("models", [])}
        want = s.vlm_model
        return want in names or (":" not in want and f"{want}:latest" in names)
    except Exception:
        return False


def _clean(content: str) -> str:
    content = _THINK_RE.sub("", content or "").strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content).strip()
    return content


def _structured_chat_impl(system: str, user: str, schema: type[BaseModel], images: list[Path] | None,
                          retries: int, temperature: float, timeout_s: float | None) -> tuple[BaseModel, int]:
    s = get_settings()
    client = _client(timeout_s if timeout_s is not None else s.http_timeout_s)
    user_msg: dict[str, Any] = {"role": "user", "content": user}
    if images:
        user_msg["images"] = [str(p) for p in images]
    messages: list[dict[str, Any]] = [{"role": "system", "content": system}, user_msg]
    fmt = schema.model_json_schema()
    last_err = ""
    attempts = 0
    for _ in range(retries + 1):
        attempts += 1
        try:
            resp = client.chat(model=s.vlm_model, messages=messages, format=fmt, think=False, stream=False,
                               options={"temperature": temperature, "num_ctx": NUM_CTX, "num_predict": NUM_PREDICT})
        except (ConnectionError, httpx.HTTPError, ollama.ResponseError, OSError) as e:
            raise VLMError(f"Ollama request failed ({type(e).__name__}): {e}", attempts) from e
        content = _clean(resp.message.content or "")
        truncated = getattr(resp, "done_reason", None) == "length"
        log.debug("VLM attempt %d: eval_count=%s done_reason=%s", attempts,
                  getattr(resp, "eval_count", None), getattr(resp, "done_reason", None))
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


def interpret_images(before_png: Path, after_png: Path, overlay_png: Path, *, mode: Literal["forest", "mining"],
                     claim: str, before_date: str, after_date: str, measurements: dict) -> VLMResult:
    """Ask the VLM to interpret the image triplet. Never raises; never fabricates a result."""
    s = get_settings()
    t0 = time.monotonic()
    attempts = 0
    try:
        if mode not in ("forest", "mining"):
            raise ValueError(f"unknown mode {mode!r}")
        user = build_user_prompt(mode=mode, claim=claim, before_date=before_date, after_date=after_date,
                                 measurements=measurements or {})
        with tempfile.TemporaryDirectory(prefix="vlm_") as td:
            imgs = [_prepare_image(p, Path(td), i) for i, p in enumerate((before_png, after_png, overlay_png))]
            analysis, attempts = _structured_chat_impl(
                SYSTEM_PROMPT, user, VisualAnalysis, imgs, retries=2, temperature=0.1,
                timeout_s=max(s.http_timeout_s, IMAGE_TIMEOUT_FLOOR_S))
        return VLMResult(status="ok", model=s.vlm_model, analysis=analysis, attempts=attempts,
                         duration_s=round(time.monotonic() - t0, 2))
    except Exception as e:  # noqa: BLE001 - contract: never raise
        attempts = getattr(e, "attempts", attempts)
        log.warning("VLM interpretation unavailable: %s", e)
        return VLMResult(status="unavailable", model=s.vlm_model, error=f"{type(e).__name__}: {e}",
                         attempts=attempts, duration_s=round(time.monotonic() - t0, 2))
