#!/usr/bin/env python3
"""TidyQuest stage 2 -- Questmaster planner (local backend).

Consumes a stage-1 inventory.json and emits a quest plan that validates
against schemas/quest.schema.json.

Locked decisions (2026-10-26, approved where noted):
- Zones become quests. The largest zone BY TIDY-ABLE COUNT is the Boss
  Battle (ties prefer the center zone -- middle center -- when it is
  tied for largest, else coverage, then zone_id). Quests are ordered
  smallest first so small wins come early; the boss, being largest, lands
  last.
- FILTER (APPROVED 2026-10-26): `structure` is never a quest item, and
  `furniture` is never a quest item in vehicle interiors (car seats are
  not tidy-able). Filtered items never appear in item_refs or quest counts.
  Space type is passed EXPLICITLY by every caller (default "room"; a
  vehicle interior must be declared) -- it is never inferred from a
  filename substring, so a user photo like "carpet_cleanup.jpg" can
  never be misclassified as a vehicle interior.
- Within a zone, steps are ordered trash-first, then categories, mystery
  last: clear-out categories (trash, paper, cardboard box) first, then remaining
  categories alphabetically, then Mystery Box steps. A step covers at most
  4 items and is one physical action under ~2 minutes.
- Energy budgets are HARD: barely-standing 300s, 10-minutes 600s,
  an-hour 3600s, full-send unlimited. Steps are taken in plan order until
  the budget is exhausted; a step is atomic (never split); quests that
  contribute no steps are dropped. est_minutes = ceil(total / 60), min 1.
- The last step of every plan is a surface reset ("surfaces last"):
  included when it still fits the budget, and ALWAYS for full-send
  (the flagship mode must end with it).
- The intro line is keyed to the plan's ACTUAL minutes band, not the
  level picked: a plan that takes 18 minutes says "up to an hour",
  never wears an hour's intro. Rerouted plans recompute the intro from
  their new total (except the skip-all rest plan, which keeps its rest
  line).
- Mystery Box: category == 'unknown' OR confidence < 0.7 (the placeholder
  tiers are 0.7 vlm / 0.3 heuristic, so < 0.7 means unmeasured).
- No-shame re-route: reroute() removes skipped steps, recomputes
  est_minutes, never scolds. If every step is skipped, the plan becomes a
  single rest step.
- This module is the LOCAL backend. The Muse Spark backend lives in
  spark.py and is coded but NOT called until free credits are confirmed.
"""

import math
from copy import deepcopy

ENERGY_BUDGETS = {
    "barely-standing": 300,
    "10-minutes": 600,
    "an-hour": 3600,
    "full-send": None,  # unlimited
}

EXCLUDED_ALWAYS = {"structure"}
VEHICLE_EXCLUDED = {"furniture"}  # car seats etc. are not tidy-able

MAX_ITEMS_PER_STEP = 4
STEP_BASE_SECONDS = 25
STEP_PER_ITEM_SECONDS = 25
STEP_MAX_SECONDS = 115
SURFACE_STEP_SECONDS = 60
REST_STEP_SECONDS = 60

CLEAR_OUT_FIRST = ["trash", "paper", "cardboard box"]  # trash-first ordering

# Intro lines keyed to the plan's ACTUAL minutes band (C6 fix): the
# level picked is a cap, never a promise. A plan that takes 18 minutes
# says "up to an hour" -- it never wears an hour's intro line.
def _intro_for_minutes(est_minutes):
    if est_minutes <= 5:
        return (
            "A few minutes is plenty. We'll take the smallest win first, "
            "and that's enough."
        )
    if est_minutes <= 10:
        return (
            "Ten minutes, one small win at a time. You've got this, "
            "and there's no rush."
        )
    if est_minutes <= 60:
        return (
            "Up to an hour of steady wins. We'll work zone by zone, "
            "and every cleared spot counts."
        )
    return (
        "Full send! We'll work through the whole space, one quest "
        "at a time, until it's reclaimed."
    )


INTRO_LINES = {
    # Kept for callers that still key by level; plan() and reroute()
    # use _intro_for_minutes() (actual minutes band) instead.
    "barely-standing": _intro_for_minutes(5),
    "10-minutes": _intro_for_minutes(10),
    "an-hour": _intro_for_minutes(60),
    "full-send": _intro_for_minutes(61),
}

REROUTE_RULE = (
    "Skipped a step? No problem at all -- the Questmaster quietly "
    "re-plans around it. Nothing scolds, no streak breaks, and the "
    "quest waits for you, not the other way around."
)

RESCAN_PROMPT = (
    "When you're done, snap the after photo -- the Questmaster counts "
    "what's left, so your progress is proven, not guessed."
)

CARD_TEXT_TEMPLATE = "{items} items · {minutes} minutes · 1 space reclaimed"

SPACE_TITLES = {
    "donna bathroom": "Donna's Bathroom",
    "car backseat": "Car Backseat",
}

REST_INSTRUCTION = (
    "Rest first -- the room will wait. Come back when you have a "
    "little energy."
)

SURFACE_INSTRUCTION = "Wipe down the cleared surfaces."

# (verb phrase builder, singular noun, plural noun); None noun = uncountable,
# instruction written without a count.
CATEGORY_PHRASES = {
    "bag": ("Gather", "bag", "bags", "in {zone} and put {pron} in one spot"),
    "bottle": ("Collect", "bottle", "bottles", "in {zone} and put {pron} in recycling"),
    "towel": ("Fold", "towel", "towels", "in {zone} and put {pron} with the laundry"),
    "book": ("Shelve", "book", "books", "in {zone}"),
    "cable": ("Coil", "cable", "cables", "in {zone} and put {pron} in one spot"),
    "electronic": ("Put", "electronic item", "electronic items", "in {zone} where they live"),
    "cleaning supply": ("Put", "cleaning supply", "cleaning supplies", "in {zone} back in their spot"),
    "toiletry": ("Put", "toiletry item", "toiletries", "in {zone} back on the shelf"),
    "cardboard box": ("Break down", "cardboard box", "cardboard boxes", "in {zone} and stack {pron} flat"),
    "paper": (None, None, None, "in {zone} into one pile -- recycle what's trash, keep what's not"),
    "bedding": (None, None, None, "in {zone} and put it away"),
    "clothing": (None, None, None, "in {zone} into the laundry pile"),
    # Trash is thrown OUT, never "tidied in place" (and never "trashs"):
    # "Throw out 2 bags of trash in upper center".
    "trash": ("Throw out", "bag of trash", "bags of trash", "in {zone}"),
}


def excluded_categories(space_type):
    excluded = set(EXCLUDED_ALWAYS)
    if space_type == "vehicle":
        excluded |= VEHICLE_EXCLUDED
    return excluded


def is_mystery(instance):
    """Unknown category, or a label too uncertain to act on directly."""
    return instance["category"] == "unknown" or instance.get("confidence", 1) < 0.7


def plan_title(image_id):
    """'test_05_donna_bathroom_BEFORE.jpg' -> \"Donna's Bathroom Reclaim\"."""
    name = (image_id or "room").rsplit(".", 1)[0]
    for prefix in ("test_01_", "test_05_", "test_"):
        if name.startswith(prefix):
            name = name[len(prefix):]
            break
    for suffix in ("_before", "_after"):
        if name.lower().endswith(suffix):
            name = name[: -len(suffix)]
            break
    key = name.lower().replace("_", " ")
    display = SPACE_TITLES.get(key, key.title())
    return f"{display} Reclaim"


def _qty(n, singular, plural):
    return f"{n} {singular if n == 1 else plural}"


def _pron(n):
    return "it" if n == 1 else "them"


def _category_instruction(category, n, zone_label):
    if category == "paper":
        return f"Gather the loose paper {CATEGORY_PHRASES['paper'][3].format(zone=zone_label)}"
    if category in ("bedding", "clothing"):
        verb = "Fold" if category == "bedding" else "Gather"
        return f"{verb} the {category} {CATEGORY_PHRASES[category][3].format(zone=zone_label)}"
    verb, singular, plural, tail = CATEGORY_PHRASES.get(
        category, ("Tidy", category, category + "s", "in {zone}")
    )
    return f"{verb} {_qty(n, singular, plural)} {tail.format(zone=zone_label, pron=_pron(n))}"


def _mystery_instruction(zone_label):
    return (
        f"Mystery Box in {zone_label}: open it, sort what you find "
        f"-- keep, toss, or rehome."
    )


def step_seconds(n_items):
    return min(STEP_MAX_SECONDS, STEP_BASE_SECONDS + STEP_PER_ITEM_SECONDS * n_items)


def _chunk(items, size=MAX_ITEMS_PER_STEP):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _zone_steps(zone_id, zone_label, instances):
    """Ordered steps for one zone: clear-out, categories, mystery last."""
    known = [i for i in instances if not is_mystery(i)]
    mystery = [i for i in instances if is_mystery(i)]
    by_category = {}
    for inst in known:
        by_category.setdefault(inst["category"], []).append(inst)
    ordered_cats = [c for c in CLEAR_OUT_FIRST if c in by_category]
    ordered_cats += sorted(c for c in by_category if c not in CLEAR_OUT_FIRST)
    steps = []
    n = 0
    for cat in ordered_cats:
        for group in _chunk(by_category[cat]):
            n += 1
            steps.append(
                {
                    "id": f"{zone_id.replace(' ', '-')}-s{n}",
                    "instruction": _category_instruction(cat, len(group), zone_label),
                    "est_seconds": step_seconds(len(group)),
                    "item_refs": [i["id"] for i in group],
                }
            )
    for group in _chunk(mystery):
        n += 1
        steps.append(
            {
                "id": f"{zone_id.replace(' ', '-')}-s{n}",
                "instruction": _mystery_instruction(zone_label),
                "est_seconds": step_seconds(len(group)),
                "is_mystery_box": True,
                "item_refs": [i["id"] for i in group],
            }
        )
    return steps


def _boss_zone_id(zone_stats):
    """Zone with max tidy count; ties prefer the center zone.

    zone_stats: list of (zone_id, tidy_count, coverage_pct). If the
    center zone ("middle center") is tied for the largest tidy count,
    it is the Boss Battle -- the narration promise is "the center pile
    is the Boss Battle", and a tie must not crown a side zone over it.
    Otherwise ties break by coverage, then zone_id.
    """
    if not zone_stats:
        return None
    max_count = max(z[1] for z in zone_stats)
    tied = [z for z in zone_stats if z[1] == max_count]
    for z in tied:
        if z[0] == "middle center":
            return "middle center"
    ordered = sorted(tied, key=lambda z: (z[2], z[0]))
    return ordered[-1][0]


def plan(inventory, energy_level, space_type="room"):
    """Build a quest plan dict from an inventory dict.

    Raises ValueError on an unknown energy level. Never exceeds the
    energy budget (except full-send, which is unlimited). space_type
    is explicit ("room" default; "vehicle" must be passed for vehicle
    interiors) -- it is NEVER inferred from the image filename.
    """
    if energy_level not in ENERGY_BUDGETS:
        raise ValueError(
            f"unknown energy_level {energy_level!r}; "
            f"expected one of {sorted(ENERGY_BUDGETS)}"
        )
    if space_type not in ("room", "vehicle"):
        raise ValueError(
            f"unknown space_type {space_type!r}; expected 'room' or 'vehicle'"
        )
    excluded = excluded_categories(space_type)
    tidy = [i for i in inventory["instances"] if i["category"] not in excluded]

    zones_by_id = {z["zone_id"]: z for z in inventory["zones"]}
    by_zone = {}
    for inst in tidy:
        by_zone.setdefault(inst["zone_id"], []).append(inst)

    zone_stats = [
        (zid, len(items), zones_by_id.get(zid, {}).get("coverage_pct", 0))
        for zid, items in by_zone.items()
    ]
    boss_id = _boss_zone_id(zone_stats) if zone_stats else None
    ordered_zones = sorted(zone_stats, key=lambda z: (z[1], z[2], z[0]))

    budget = ENERGY_BUDGETS[energy_level]
    quests = []
    running_total = 0
    for zid, _count, _cov in ordered_zones:
        label = zones_by_id.get(zid, {}).get("label", zid)
        steps = _zone_steps(zid, label, by_zone[zid])
        kept = []
        for step in steps:
            if budget is not None and running_total + step["est_seconds"] > budget:
                continue
            kept.append(step)
            running_total += step["est_seconds"]
        if not kept:
            continue
        title = f"{label.title()} Quest"
        if zid == boss_id:
            title += " -- Boss Battle"
        quests.append(
            {
                "zone_id": zid,
                "title": title,
                "steps": kept,
                **({"is_boss": True} if zid == boss_id else {}),
            }
        )

    if quests and (budget is None or running_total + SURFACE_STEP_SECONDS <= budget):
        # Full-send (budget None) ALWAYS ends with the surface reset:
        # the flagship "do everything" mode must finish with it.
        quests[-1]["steps"].append(
            {
                "id": "surfaces-s1",
                "instruction": SURFACE_INSTRUCTION,
                "est_seconds": SURFACE_STEP_SECONDS,
            }
        )
        running_total += SURFACE_STEP_SECONDS

    if not quests:
        # Nothing tidy-able (or nothing fit): a warm, valid, minimal plan.
        first_zone = inventory["zones"][0] if inventory["zones"] else {"zone_id": "room", "label": "room"}
        quests = [
            {
                "zone_id": first_zone["zone_id"],
                "title": "Already Clear",
                "steps": [
                    {
                        "id": "clear-s1",
                        "instruction": (
                            "This space already looks clear -- take a breath, "
                            "you earned it."
                        ),
                        "est_seconds": REST_STEP_SECONDS,
                    }
                ],
            }
        ]
        running_total = REST_STEP_SECONDS

    # Intro keyed to the plan's ACTUAL minutes band (C6): the level
    # picked is a cap, never a promise -- an 18-minute "an-hour" plan
    # says "up to an hour", not "an hour of steady wins".
    est_minutes = max(1, math.ceil(running_total / 60))
    return {
        "quest_title": plan_title(inventory.get("image_id", "")),
        "energy_level": energy_level,
        "est_minutes": est_minutes,
        "intro_line": _intro_for_minutes(est_minutes),
        "quests": quests,
        "reroute_rule": REROUTE_RULE,
        "completion": {
            "card_text": CARD_TEXT_TEMPLATE,
            "rescan_prompt": RESCAN_PROMPT,
        },
    }


def reroute(plan_dict, skipped_step_ids):
    """Return a new plan with the skipped steps removed, no scolding.

    Quests left empty are dropped. If every step is skipped, the plan
    becomes a single rest step. est_minutes is recomputed AND the
    intro_line is recomputed from the new minutes band, so a rerouted
    plan never keeps a stale intro; the budget still holds because a
    re-plan can only shrink.
    """
    skipped = set(skipped_step_ids)
    new_plan = deepcopy(plan_dict)
    new_quests = []
    total = 0
    for quest in new_plan["quests"]:
        kept = [s for s in quest["steps"] if s["id"] not in skipped]
        if not kept:
            continue
        quest["steps"] = kept
        total += sum(s["est_seconds"] for s in kept)
        new_quests.append(quest)
    if not new_quests:
        new_plan["quests"] = [
            {
                "zone_id": "rest",
                "title": "Rest Quest",
                "steps": [
                    {
                        "id": "rest-1",
                        "instruction": REST_INSTRUCTION,
                        "est_seconds": REST_STEP_SECONDS,
                    }
                ],
            }
        ]
        new_plan["est_minutes"] = 1
        new_plan["intro_line"] = (
            "That's completely fine -- rest now, and the quest will be "
            "here when you're ready."
        )
    else:
        new_plan["quests"] = new_quests
        new_plan["est_minutes"] = max(1, math.ceil(total / 60))
        new_plan["intro_line"] = _intro_for_minutes(new_plan["est_minutes"])
    return new_plan
