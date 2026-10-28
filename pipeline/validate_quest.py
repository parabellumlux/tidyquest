#!/usr/bin/env python3
"""Validate a TidyQuest quest plan: JSON Schema + budget + partition +
boss + mystery rules. Usage: validate_quest.py <plan.json> <inventory.json>
[--space-type room|vehicle]. Exit 0 on PASS, 1 on FAIL.
"""
import argparse
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import quest as quest_mod

import jsonschema

def check(plan_path, inv_path, space_type=None, rerouted=False):
    """Validate a plan. Set rerouted=True for reroute() outputs: a
    re-plan legitimately drops skipped items, so the full-send
    completeness rule does not apply to it."""
    plan = json.load(open(plan_path))
    inv = json.load(open(inv_path))
    errs = []

    schema_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "schemas", "quest.schema.json"
    )
    schema = json.load(open(schema_path))
    try:
        jsonschema.validate(plan, schema)
    except jsonschema.ValidationError as exc:
        errs.append(f"schema violation: {exc.message}")

    level = plan.get("energy_level")
    if level not in quest_mod.ENERGY_BUDGETS:
        errs.append(f"unknown energy_level {level!r}")
        return errs
    budget = quest_mod.ENERGY_BUDGETS[level]

    space_type = space_type or "room"
    excluded = quest_mod.excluded_categories(space_type)
    tidy = [i for i in inv["instances"] if i["category"] not in excluded]
    tidy_by_id = {i["id"]: i for i in tidy}

    total_seconds = 0
    seen_refs = []
    for q in plan.get("quests", []):
        for s in q.get("steps", []):
            total_seconds += s.get("est_seconds", 0)
            if s.get("est_seconds", 0) > 120:
                errs.append(f"step {s['id']} est_seconds {s['est_seconds']} exceeds ~2 min")
            for ref in s.get("item_refs", []):
                seen_refs.append(ref)
                if ref not in tidy_by_id:
                    errs.append(f"step {s['id']} refs non-tidy/nonexistent instance {ref}")
                elif quest_mod.is_mystery(tidy_by_id[ref]) != bool(s.get("is_mystery_box")):
                    errs.append(
                        f"step {s['id']} mystery flag mismatch on instance {ref} "
                        f"(category {tidy_by_id[ref]['category']}, "
                        f"confidence {tidy_by_id[ref].get('confidence')})"
                    )
    if len(seen_refs) != len(set(seen_refs)):
        errs.append("item_refs are not unique across the plan")
    if budget is not None and total_seconds > budget:
        errs.append(f"plan total {total_seconds}s exceeds {level} budget {budget}s")
    if plan.get("est_minutes") != max(1, math.ceil(total_seconds / 60)):
        errs.append(
            f"est_minutes {plan.get('est_minutes')} != ceil({total_seconds}/60)"
        )
    if level == "full-send" and not rerouted:
        missing = sorted(set(tidy_by_id) - set(seen_refs))
        if missing:
            errs.append(f"full-send plan misses tidy instances: {missing}")

    bosses = [q for q in plan.get("quests", []) if q.get("is_boss")]
    if len(bosses) > 1:
        errs.append(f"expected at most 1 boss quest, found {len(bosses)}")
    elif len(bosses) == 1:
        # Boss rule: the boss flag marks the room's largest zone (by tidy
        # count, ties prefer the center zone when tied, else coverage
        # then zone_id). A budgeted plan that never reaches the boss zone
        # legitimately has NO boss; a plan that flags any other zone is
        # wrong. (Fresh full-send plans must have exactly one boss --
        # asserted in test_stage2.py, not here, because a re-routed plan
        # may legitimately lose its boss quest.)
        zones_by_id = {z["zone_id"]: z for z in inv["zones"]}
        stats = []
        for zid in {i["zone_id"] for i in tidy}:
            items = [i for i in tidy if i["zone_id"] == zid]
            stats.append(
                (zid, len(items), zones_by_id.get(zid, {}).get("coverage_pct", 0))
            )
        expected_boss = quest_mod._boss_zone_id(stats)
        if bosses[0]["zone_id"] != expected_boss:
            errs.append(
                f"boss zone {bosses[0]['zone_id']} != room's largest zone {expected_boss}"
            )
    if not plan.get("intro_line"):
        errs.append("missing intro_line")
    if not plan.get("reroute_rule"):
        errs.append("missing reroute_rule")
    if not plan.get("quests"):
        errs.append("plan has no quests")
    return errs


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("plan")
    parser.add_argument("inventory")
    parser.add_argument("--space-type", choices=["room", "vehicle"], default=None)
    args = parser.parse_args()
    errs = check(args.plan, args.inventory, args.space_type)
    print(("PASS " if not errs else "FAIL ") + args.plan)
    for e in errs:
        print("   ", e)
    return 0 if not errs else 1


if __name__ == "__main__":
    raise SystemExit(main())
