#!/usr/bin/env python3
"""Muse Spark integration point -- LIVE since C4 (2026-10-27).

The $150 Meta Model API credit was confirmed in place by DC and the
key file verified live (GET /v1/models 200 + metered completion 200)
on 2026-10-27, so this module now performs real network calls through
the registered connector custom.meta-model-api (host api.meta.ai).
The local planner (quest.py) remains the fallback when no BYOK key is
supplied or a live call fails.

BYOK: this module NEVER touches credentials. When live, the caller's
API key is injected per request via the api_key parameter (supplied by
the app layer from the user's own settings field) -- it is never read
from environment or disk inside this module, never logged, and never
present in any output. No API key value appears in this file, in logs,
or in any output.

Model ids (VERIFIED 2026-10-26 from Baymax's live Meta Model API
catalog -- no longer guessed): muse-spark-1.2 and muse-spark-1.3 for
the planner call (default muse-spark-1.2, the id Baymax's 200-returning
test call used); sam-3.1 for segmentation in the same catalog.

CONNECTOR (RESOLVED 2026-10-26): the registered connector is
"custom.meta-model-api" (provider slug meta-model-api, host
api.meta.ai), confirmed by Baymax from the vault side. The
"custom.meta-llama-api" name in older docstrings was never a real
registration. Live calls below go to https://api.meta.ai/v1 with a
Bearer key; they do not go through any vault connector object.
"""

SPARK_ENABLED = True  # flipped 2026-10-27: $150 credit + key verified live

# Verified 2026-10-26 from the live Meta Model API model catalog.
SPARK_MODELS = ("muse-spark-1.2", "muse-spark-1.3")
DEFAULT_SPARK_MODEL = "muse-spark-1.2"


def build_planner_prompt(inventory, energy_level, budget_seconds):
    """The prompt Spark would receive for one planning call.

    Returns a string. The inventory JSON is embedded verbatim; Spark is
    instructed to return ONLY a JSON quest plan matching
    schemas/quest.schema.json, sized to the energy budget, ordered
    trash-first, with the largest zone as Boss Battle, low-confidence /
    unknown items as Mystery Box steps, and a warm, zero-shame tone.
    """
    import json

    budget_text = (
        "no time limit" if budget_seconds is None else f"{budget_seconds} seconds"
    )
    return (
        "You are the TidyQuest Questmaster, a warm, encouraging planner for "
        "people who feel frozen by a messy space -- never a drill sergeant, "
        "never shaming the mess.\n\n"
        "Turn this room inventory into a quest plan matching the "
        "TidyQuest quest schema (quest_title, energy_level, est_minutes, "
        "intro_line, quests with zone_id/title/steps, reroute_rule, "
        "completion).\n\n"
        "Rules:\n"
        "- Energy budget is HARD: the plan's total estimated time must fit "
        f"inside {budget_text} (declared level: {energy_level}); never exceed it.\n"
        "- Zones become quests; the largest zone is the Boss Battle, whose "
        "health bar is the live item count from re-scans.\n"
        "- Order steps trash-first, then categories, surfaces last.\n"
        "- Low-confidence or 'unknown' items become Mystery Box steps "
        "('open it, sort what you find').\n"
        "- Steps are HOMOGENEOUS: a step covers EITHER known items "
        "(is_mystery_box false or absent) OR unknown items "
        "(is_mystery_box true, mystery instruction only) -- never mix "
        "known and unknown items in one step.\n"
        "- 'structure' items are never quest items; in vehicle interiors, "
        "'furniture' (seats) is never a quest item either.\n"
        "- Each step is ONE physical action, completable in under ~2 minutes, "
        "with est_seconds and the inventory instance ids it clears.\n"
        "- Skipped steps are silently re-planned around: no scolding, no "
        "broken streaks.\n"
        "- Return ONLY the JSON plan, no prose.\n\n"
        "EXACT JSON SHAPE (field names matter -- use exactly these):\n"
        '{"quest_title": str, "energy_level": str, "est_minutes": int, '
        '"intro_line": str, "quests": [{"zone_id": str, "title": str, '
        '"is_boss": bool, "steps": [{"id": "<zone_id>-s1", '
        '"instruction": str, "est_seconds": int, "item_refs": '
        "[<inventory instance ids this step clears>]}]}], "
        '"reroute_rule": str, "completion": {"card_text": str}}\n'
        "Every step MUST have id, instruction, est_seconds, and item_refs. "
        "item_refs must list real instance ids from the inventory above. "
        "The largest zone is the Boss Battle (is_boss true, exactly one "
        "boss).\n\n"
        f"Inventory:\n{json.dumps(inventory, indent=2)}"
    )


def build_request(inventory, energy_level, budget_seconds, model=None):
    """Build (do NOT send) the request dict Spark would receive.

    model defaults to DEFAULT_SPARK_MODEL (muse-spark-1.2, verified
    2026-10-26 from the live Meta Model API catalog); pass
    "muse-spark-1.3" explicitly for the newer planner. response_format
    is {"type": "json_object"} -- the OpenAI-style shape the API uses,
    not a bare string.
    """
    if model is None:
        model = DEFAULT_SPARK_MODEL
    return {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": "You are the TidyQuest Questmaster. Return ONLY JSON.",
            },
            {
                "role": "user",
                "content": build_planner_prompt(
                    inventory, energy_level, budget_seconds
                ),
            },
        ],
        "response_format": {"type": "json_object"},
    }


def _unwrap_envelope(payload):
    """Unwrap a chat-completions envelope to the plan payload.

    A real Meta Model API response is an envelope: the quest plan
    arrives as a JSON *string* inside choices[0].message.content. A
    direct plan dict (or a JSON string of one) passes through
    unchanged. Raises ValueError on anything unrecognizable.
    """
    import json

    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Spark response is not valid JSON: {exc}") from exc
    if isinstance(payload, dict) and "choices" in payload:
        choices = payload.get("choices") or []
        if not choices or not isinstance(choices[0], dict):
            raise ValueError("Spark response envelope has no choices")
        message = choices[0].get("message") or {}
        content = message.get("content")
        if content is None:
            raise ValueError("Spark response envelope has no message content")
        if isinstance(content, str):
            try:
                return json.loads(content)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Spark plan inside envelope is not valid JSON: {exc}"
                ) from exc
        return content
    return payload


def parse_response(payload):
    """Validate a Spark response payload into a quest plan dict.

    Accepts a direct plan dict, a JSON string of one, or a full
    chat-completions envelope (unwrapped first). Requires the quest
    schema's required fields; returns the plan dict unchanged
    otherwise. Raises ValueError on anything malformed -- a bad Spark
    response never becomes a plan.
    """
    import json

    payload = _unwrap_envelope(payload)
    if not isinstance(payload, dict):
        raise ValueError("Spark response must be a JSON object")
    required = ["quest_title", "energy_level", "est_minutes", "quests"]
    missing = [k for k in required if k not in payload]
    if missing:
        raise ValueError(f"Spark plan missing required fields: {missing}")
    for quest in payload["quests"]:
        for key in ("zone_id", "title", "steps"):
            if key not in quest:
                raise ValueError(f"Spark quest missing {key!r}")
        for step in quest["steps"]:
            for key in ("id", "instruction", "est_seconds"):
                if key not in step:
                    raise ValueError(f"Spark step missing {key!r}")
    return payload


API_BASE_URL = "https://api.meta.ai/v1"


def normalize_plan(plan, inventory):
    """Enforce the quest schema's mystery homogeneity rule (C4 fix).

    validate_quest requires every step's item_refs to agree with its
    is_mystery_box flag (quest.is_mystery: category "unknown" or
    confidence < 0.7). Spark mixes unknown items into regular steps,
    which rightly fails validation. Normalization splits mixed steps
    into a known part (original id/instruction) and a mystery part
    (id + "-mystery", canonical mystery instruction, flag True),
    preserving total seconds exactly, then recomputes est_minutes.
    Refs that do not exist in the inventory are left untouched so
    validation still catches genuinely bad Spark output -- this
    function never launders errors, it only enforces the flag rule.
    """
    import math

    from quest import _mystery_instruction, is_mystery

    by_id = {i["id"]: i for i in inventory.get("instances", [])}
    total_seconds = 0
    for quest in plan.get("quests", []):
        new_steps = []
        for step in quest.get("steps", []):
            refs = step.get("item_refs", [])
            known = [r for r in refs
                     if r in by_id and not is_mystery(by_id[r])]
            mystery = [r for r in refs
                       if r in by_id and is_mystery(by_id[r])]
            other = [r for r in refs if r not in by_id]
            seconds = step.get("est_seconds", 0)
            if mystery and known:
                mystery_seconds = max(1, round(
                    seconds * len(mystery) / len(refs))) if refs else 0
                known_seconds = seconds - mystery_seconds
                if known_seconds < 1:
                    known_seconds, mystery_seconds = 1, seconds
                known_step = dict(step)
                known_step["item_refs"] = known + other
                known_step["est_seconds"] = known_seconds
                known_step["is_mystery_box"] = False
                mystery_step = dict(step)
                mystery_step["id"] = step["id"] + "-mystery"
                mystery_step["instruction"] = _mystery_instruction(
                    quest.get("zone_id", ""))
                mystery_step["item_refs"] = mystery
                mystery_step["est_seconds"] = mystery_seconds
                mystery_step["is_mystery_box"] = True
                new_steps.extend([known_step, mystery_step])
                total_seconds += known_seconds + mystery_seconds
            elif mystery:
                fixed = dict(step)
                fixed["is_mystery_box"] = True
                fixed["instruction"] = _mystery_instruction(
                    quest.get("zone_id", ""))
                new_steps.append(fixed)
                total_seconds += seconds
            else:
                fixed = dict(step)
                fixed["is_mystery_box"] = False
                new_steps.append(fixed)
                total_seconds += seconds
        quest["steps"] = new_steps
    plan["est_minutes"] = max(1, math.ceil(total_seconds / 60))
    return plan


def plan_with_spark(inventory=None, energy_level=None, budget_seconds=None,
                    model=None, api_key=None):
    """The live Spark planning call (LIVE since C4, 2026-10-27).

    POSTs the built request to /v1/chat/completions and validates the
    result with parse_response (a malformed Spark response never
    becomes a plan). Raises RuntimeError if SPARK_ENABLED is False and
    ValueError without an api_key.

    BYOK: api_key is the user's own Meta Model API key, injected per
    request by the caller (the app layer's settings field, via the
    proxy) -- this module never reads it from environment or disk,
    never logs it, and never puts it in output.

    NOTE on tokens: muse-spark-1.2 is a reasoning model -- observed
    2026-10-27 it spends ~4,000 reasoning tokens before answering, so
    max_tokens=16000 leaves room for reasoning + the plan. With a
    4096 cap the model burns the whole budget reasoning and returns
    content=None, which parse_response rightly refuses.
    """
    import json
    import urllib.request

    if not SPARK_ENABLED:
        raise RuntimeError("Spark backend disabled.")
    if inventory is None or energy_level is None:
        raise ValueError("plan_with_spark requires inventory and energy_level")
    if not api_key:
        raise ValueError("plan_with_spark requires api_key (BYOK)")
    request = build_request(inventory, energy_level, budget_seconds,
                            model=model)
    request["max_tokens"] = 16000
    http_req = urllib.request.Request(
        API_BASE_URL + "/chat/completions",
        data=json.dumps(request).encode(),
        headers={"Authorization": "Bearer " + api_key,
                 "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(http_req, timeout=300) as resp:
        payload = json.loads(resp.read().decode())
    return normalize_plan(parse_response(payload), inventory)
