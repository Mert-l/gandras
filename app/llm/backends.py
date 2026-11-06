"""HTTP backends for non-Ollama providers. Each returns (raw_text, truncated) for the shared
validate-and-retry loop in app.vlm.client; none of them computes or alters results."""
from __future__ import annotations

import base64
import json
from pathlib import Path

import httpx

from app.llm.registry import ModelProfile


class BackendError(RuntimeError):
    pass


def _b64(path: Path) -> str:
    return base64.b64encode(Path(path).read_bytes()).decode("ascii")


def _first_user_index(messages: list[dict]) -> int:
    return next(i for i, m in enumerate(messages) if m["role"] == "user")


def _error(provider: str, r: httpx.Response) -> BackendError:
    try:
        body = r.json()
        msg = (body.get("error") or {}).get("message") if isinstance(body.get("error"), dict) else body.get("error")
    except ValueError:
        msg = r.text[:200]
    return BackendError(f"{provider} HTTP {r.status_code}: {str(msg or '')[:200]}")


def openai_chat(p: ModelProfile, messages: list[dict], schema: dict, schema_name: str, images: list[Path] | None,
                temperature: float, timeout_s: float, client: httpx.Client | None = None) -> tuple[str, bool]:
    """OpenAI-compatible /chat/completions with json_schema output, falling back to json_object
    for servers that do not support schema-constrained output."""
    msgs = [dict(m) for m in messages]
    if images:
        i = _first_user_index(msgs)
        msgs[i]["content"] = [{"type": "text", "text": msgs[i]["content"]}] + [
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + _b64(img)}} for img in images]
    headers = {"Authorization": f"Bearer {p.api_key()}"} if p.api_key() else {}
    url = p.base_url.rstrip("/") + "/chat/completions"
    body = {"model": p.model, "messages": msgs, "temperature": temperature, "max_tokens": p.max_tokens,
            "response_format": {"type": "json_schema", "json_schema": {"name": schema_name[:60], "schema": schema}}}
    own = client is None
    client = client or httpx.Client(timeout=timeout_s, trust_env=False)
    try:
        r = client.post(url, json=body, headers=headers)
        if r.status_code == 400 and "response_format" in r.text:
            # Server lacks json_schema support: ask for a JSON object and put the schema in the prompt.
            body["response_format"] = {"type": "json_object"}
            body["messages"] = [{"role": "system", "content": msgs[0]["content"] + "\nReturn ONLY a JSON object matching "
                                 "this JSON schema:\n" + json.dumps(schema)}] + msgs[1:]
            r = client.post(url, json=body, headers=headers)
        if r.status_code >= 400:
            raise _error("OpenAI-compatible server", r)
        choice = r.json()["choices"][0]
        return choice["message"].get("content") or "", choice.get("finish_reason") == "length"
    except httpx.HTTPError as e:
        raise BackendError(f"OpenAI-compatible request failed ({type(e).__name__})") from e
    finally:
        if own:
            client.close()


def anthropic_chat(p: ModelProfile, messages: list[dict], schema: dict, schema_name: str, images: list[Path] | None,
                   temperature: float, timeout_s: float, client: httpx.Client | None = None) -> tuple[str, bool]:
    """Anthropic Messages API; structured output via a single forced tool whose input_schema is the schema."""
    system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
    convo = [dict(m) for m in messages if m["role"] != "system"]
    if images:
        i = _first_user_index(convo)
        convo[i]["content"] = [{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": _b64(img)}}
                               for img in images] + [{"type": "text", "text": convo[i]["content"]}]
    tool = {"name": "submit_result", "description": f"Return the {schema_name} result.", "input_schema": schema}
    body = {"model": p.model, "max_tokens": p.max_tokens, "temperature": temperature, "system": system,
            "messages": convo, "tools": [tool], "tool_choice": {"type": "tool", "name": "submit_result"}}
    headers = {"x-api-key": p.api_key(), "anthropic-version": "2023-06-01", "content-type": "application/json"}
    own = client is None
    client = client or httpx.Client(timeout=timeout_s, trust_env=False)
    try:
        r = client.post(p.base_url.rstrip("/") + "/v1/messages", json=body, headers=headers)
        if r.status_code >= 400:
            raise _error("Anthropic", r)
        data = r.json()
        block = next((b for b in data.get("content", []) if b.get("type") == "tool_use"), None)
        if block is None:
            text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
            return text, data.get("stop_reason") == "max_tokens"
        return json.dumps(block.get("input", {})), data.get("stop_reason") == "max_tokens"
    except httpx.HTTPError as e:
        raise BackendError(f"Anthropic request failed ({type(e).__name__})") from e
    finally:
        if own:
            client.close()
