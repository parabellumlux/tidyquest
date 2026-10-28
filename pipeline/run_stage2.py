#!/usr/bin/env python3
"""Run stage 2: inventory.json + energy level -> quest plan JSON.
Usage: run_stage2.py <inventory.json> <energy_level> [--space-type room|vehicle]
       [--out plan.json]
Space type is explicit (default "room"; pass "vehicle" for vehicle
interiors) -- it is never inferred from the inventory filename.
Prints a human summary to stdout. Exits non-zero on validation failure.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import quest as quest_mod
import validate_quest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("inventory")
    parser.add_argument("energy_level", choices=sorted(quest_mod.ENERGY_BUDGETS))
    parser.add_argument("--space-type", choices=["room", "vehicle"], default="room")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    inv = json.load(open(args.inventory))
    space_type = args.space_type
    plan = quest_mod.plan(inv, args.energy_level, space_type)
    out_path = args.out
    if out_path is None:
        base = os.path.splitext(os.path.basename(args.inventory))[0]
        out_path = f"out/stage2_{base}_{args.energy_level}.json"
    with open(out_path, "w") as fh:
        json.dump(plan, fh, indent=2)

    excluded = quest_mod.excluded_categories(space_type)
    tidy_total = sum(
        1 for i in inv["instances"] if i["category"] not in excluded
    )
    covered = sum(len(s.get("item_refs", [])) for q in plan["quests"] for s in q["steps"])
    total_seconds = sum(s["est_seconds"] for q in plan["quests"] for s in q["steps"])
    budget = quest_mod.ENERGY_BUDGETS[args.energy_level]
    print(f"plan: {out_path}")
    print(f"  title: {plan['quest_title']} | energy: {args.energy_level} | space: {space_type}")
    print(f"  tidy items: {tidy_total} (filtered out: {len(inv['instances']) - tidy_total})")
    print(f"  quests: {len(plan['quests'])} | steps: {sum(len(q['steps']) for q in plan['quests'])}")
    print(f"  items covered: {covered}/{tidy_total}")
    print(f"  total: {total_seconds}s = {plan['est_minutes']} min"
          + (f" (budget {budget}s)" if budget is not None else " (full-send)"))
    print(f"  boss: {[q['zone_id'] for q in plan['quests'] if q.get('is_boss')]}")
    print(f"  intro: {plan['intro_line']}")

    errs = validate_quest.check(out_path, args.inventory, space_type)
    print(("VALIDATION PASS" if not errs else "VALIDATION FAIL"))
    for e in errs:
        print("   ", e)
    return 0 if not errs else 1


if __name__ == "__main__":
    raise SystemExit(main())
