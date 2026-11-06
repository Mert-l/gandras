# Choosing the AI model

Every investigation runs on one model, chosen in the UI's **Model** dropdown (or `options.model` in
`POST /investigations`). That model reads the claim, plans the agent's next steps, researches the
location, and — if it accepts images — interprets the satellite imagery. All measurements and the
verdict rules are deterministic code; switching models never changes how areas are measured.

`GET /models` lists what the server can use:

```json
{"default": "ollama:qwen3.5:4b",
 "models": [{"id": "ollama:qwen3.5:4b", "label": "Qwen 3.5 4B", "provider": "ollama", "model": "qwen3.5:4b",
             "location": "local", "vision": true, "available": true, "status": "ready", "note": "4.2B parameters"}]}
```

The browser sends only a model **id**. Endpoints and API-key variable names live in server config, so
a request cannot point the server at an arbitrary URL, and keys are never returned by the API.

## Sources of models

| Source | How to add | Notes |
|---|---|---|
| Local Ollama | `ollama pull <model>` | Discovered automatically from `OLLAMA_URL`; image support read from Ollama's capabilities. |
| Remote Ollama (on-prem GPU box) | entry with `"provider": "ollama", "base_url": ...` | All models installed there are discovered. |
| OpenAI-compatible server | `"provider": "openai"` | vLLM, LM Studio, llama.cpp server, TGI, OpenAI, OpenRouter, Azure OpenAI-compatible gateways. Uses `/chat/completions` with JSON-schema output, falling back to JSON mode. |
| Anthropic | `"provider": "anthropic"` | Messages API; structured output via a forced tool call. |

Copy `config/models.example.json` to `config/models.json` (git-ignored) and edit. API keys go in
environment variables named by `api_key_env` (e.g. `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`), never in
the file. Set `DEFAULT_MODEL` in `.env` to make another model the default.

Availability checks never send a prompt: Ollama models are available when installed; OpenAI-compatible
servers when `GET {base_url}/models` answers; cloud entries need their key variable set.

## Text-only models

A model without image input can still run investigations. The agent records a
`skip_visual_interpretation` step up front, and the verdict uses measurements only (it never depends
on the vision model anyway — VLM agreement only adjusts confidence).

## Tested

- Local Ollama `qwen3.5:4b` (vision) and `gemma4:latest` (text-only) through the full pipeline.
- The OpenAI-compatible backend against Ollama's own `/v1` endpoint, with images and schema output.
  (Ollama's OpenAI endpoint ignores the "no thinking" switch the native path uses, so small reasoning
  models are slower there — ~75 s for an image interpretation vs ~25 s natively.)
- Anthropic and OpenAI wire formats with recorded HTTP contracts in `tests/test_models.py`
  (no live cloud call: no keys were configured).

## Separate planning and vision models

The form has two dropdowns: **Planning model** (reads the claim, plans the agent, researches the
location and open sources) and **Vision model** (interprets the imagery; only image-capable models,
default "Same as planning model"). API: `options.model` and `options.vision_model`. A text-only model
can plan while a vision model reads the images; the header and report show both.
Tested live: Gemma4 planning + Qwen 3.5 4B vision on the Bingham Canyon case.

## Open-source context when the satellite evidence is weak

If the satellite verdict is inconclusive or low-confidence, or no usable imagery was found, the agent
adds a `research_open_sources` step: the planning model writes up to two web queries, the server
fetches up to five pages (same SSRF-safe fetcher as location discovery), and the model extracts quotes
labelled supports / contradicts / context. A quote is kept only if it appears verbatim in the fetched
page. Findings appear in the Analysis tab, the report (section 2) and the sources list, and **never
change the satellite verdict** — web text is context, not measurement. Needs a search provider
(`TAVILY_API_KEY` etc.); without one the step reports that it is unavailable.
