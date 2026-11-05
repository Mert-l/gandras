# Architecture

```
Browser (app/static: vanilla JS)  ──poll──►  FastAPI (app/api/routes.py)
                                                │ POST /investigations → 202
                                                ▼
                         services/investigations.py  (1 background worker, SQLite state)
                                                │
                                                ▼
                         agent/orchestrator.py  — bounded loop
                           ├─ planner.allowed_actions(state)   which actions are valid now
                           ├─ planner.llm_decision(...)        Qwen picks one (schema-constrained), validated
                           │    └─ rule_decision(...)          deterministic fallback
                           ├─ claims.py                        claim → (indicator, asserts, direction), LLM + rule cross-check
                           └─ tools.py                         executes the action
                                ├─ satellite/  STAC search, local SCL quality, calibrated grid-aligned bands
                                ├─ indicators/ registry: forest · vegetation · surface · built-up · water · burn · generic
                                ├─ analysis/   shared index maths · regions · PNG products
                                ├─ vlm/        Qwen visual interpretation (never computes numbers)
                                └─ corroborate  independent extra scene → persistence of change
                         verification/evaluator.py  deterministic verdict on the exact claim type
                         verification/report.py     observed / inferred / not established
```

## Responsibilities

| Layer | Decides / computes | Never does |
|---|---|---|
| Planner (LLM + rules) | which valid action next, and why | compute numbers, invent steps |
| Satellite + analysis | scenes, quality, areas, regions | interpret cause |
| VLM | what is visibly changed, alternatives, limits | measure, set the verdict alone |
| Verification engine | verdict + confidence from rules | trust model text for polarity or contradiction |

## Indicators

Each indicator (`app/indicators/*.py`) is one physical signal plus how to judge claims about it:
bands, deterministic `analyze(before, after, valid, grid, direction)`, allowed directions, evidence
label, significant/negligible areas, whether persistence must be confirmed, a confidence cap, keywords,
example claims, report wording and VLM guidance. Everything else (agent, verification, UI) is generic.

| id | Signal | Directions | Persistence required | Confidence cap |
|---|---|---|---|---|
| forest_clearing | NDVI drop from canopy-like pixels + BSI rise | decrease | no | high |
| vegetation_cover | NDVI loss / gain (any vegetation) | increase, decrease | no | medium |
| surface_disturbance | robust multispectral change (mining, earthworks) | any | yes | medium |
| built_up | new built-like surface (construction) | increase | yes | medium |
| water | MNDWI water maps on both dates | increase, decrease, any | no | high |
| burn | dNBR (B08 vs B12) + NDVI drop | increase | no | medium |
| generic_change | multispectral change (fallback) | any | yes | low; never "contradicted" |

To add an indicator: write `analyze` returning `output(result, evidence_mask, valid, other=..., previews=...,
legend=...)` with `result["evidence_ha"]` and `result["regions"]`, fill the `Indicator` fields, register
it in `app/indicators/__init__.py`, add tests in `tests/test_indicators.py` and one scouted real demo case.

## Claim interpretation

`app/agent/claims.py` reads the claim as (indicator, asserts change | no_change, direction).
The LLM chooses from the registry (or the user fixes the indicator); keyword rules cross-check:
- polarity (asserts) must match the rules, else the rules win;
- the LLM cannot pick an indicator the claim has no keywords for when another indicator's keywords match;
- explicit direction words override a conflicting LLM direction;
- the LLM cannot reject a claim that names physical/site terms; truly non-physical claims → `UNSUPPORTED_CLAIM`;
- physical claims no indicator covers → `generic_change` fallback.
Overrides are recorded in `claim_interpretation.override` and shown in the timeline.

## Agent loop

Actions: `find_before_observation`, `find_after_observation`, `search_more_imagery`,
`analyze_<indicator>`, `interpret_images`, `corroborate_change`,
`evaluate_evidence`, `finalize`.

- `allowed_actions` encodes preconditions (e.g. analysis needs both scenes; finalize needs an
  evidence assessment and an attempted visual interpretation).
- With one valid action the step is `decided_by: rule`; with several, Qwen chooses via a JSON
  schema whose `action` is a `Literal` of exactly the allowed set. Parameters are clamped.
- Invalid/failed model output → deterministic policy (marked "rule fallback").
- Budgets: `MAX_AGENT_STEPS` tool calls (default 8) and `MAX_IMAGERY_RETRIES` extra searches (2).
  Hitting the budget forces `finalize` and adds a limitation.
- Every timeline entry is written when its action starts and completed with the tool's real result.

## Untrusted input

The claim text reaches two model prompts. Guards:
- claim type from the LLM must agree with a deterministic keyword reading, else the deterministic
  reading is used (recorded in `claim_interpretation.override`); on-topic claims cannot be rejected
  by the LLM alone;
- the VLM prompt fences the claim between markers and labels it untrusted;
- indicators flagged `requires_persistence` need measured persistence to count change; VLM agreement only adjusts confidence;
- the planner can only pick from validated actions; no shell, filesystem or arbitrary network access.

## Persistence and failure

- SQLite row per investigation (full state JSON) saved after every step.
- On startup, queued/running jobs are marked `interrupted` (never `completed`).
- Execution failures (provider/IO) → `status: failed` with an error code; scientific
  insufficiency → `completed` with an `inconclusive` verdict.
- Cache (`data/cache`): STAC searches, per-scene band subsets on the analysis grid, VLM outputs.
  Keys include provider, collection, scene, grid, bands, calibration and processing version.
  Replays are marked `from_cache`.
