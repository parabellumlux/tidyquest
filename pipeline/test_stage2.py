#!/usr/bin/env python3
"""Stage 2 test suite: every inventory x every energy level validates;
budget is hard; full-send covers every tidy item exactly once; fresh
full-send plans carry exactly one boss (the room's largest zone); the
filter keeps structure (and vehicle furniture) out of every plan;
reroute removes skipped steps, recomputes est_minutes, and never scolds;
Spark integration point builds payloads but never calls anything.
Usage: test_stage2.py  (run from ~/tidyquest-build; uses .venv python)
Exit 0 only when every check passes.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import quest as quest_mod
import spark as spark_mod
import validate_quest

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "out")

CASES = [
    ("car", os.path.join(OUT, "stage1_car_before_vlm", "inventory.json"), "vehicle"),
    ("donna", os.path.join(OUT, "stage1_donna_before_vlm", "inventory.json"), "room"),
]

FAILURES = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f" -- {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


def main():
    for label, inv_path, space_type in CASES:
        inv = json.load(open(inv_path))
        excluded = quest_mod.excluded_categories(space_type)
        tidy = [i for i in inv["instances"] if i["category"] not in excluded]
        for level in quest_mod.ENERGY_BUDGETS:
            plan = quest_mod.plan(inv, level, space_type)
            tmp = f"/tmp/stage2_test_{label}_{level}.json"
            json.dump(plan, open(tmp, "w"), indent=2)
            errs = validate_quest.check(tmp, inv_path, space_type)
            check(f"{label}/{level} validates", not errs, "; ".join(errs))
            total = sum(s["est_seconds"] for q in plan["quests"] for s in q["steps"])
            budget = quest_mod.ENERGY_BUDGETS[level]
            if budget is not None:
                check(f"{label}/{level} within budget", total <= budget, f"{total} > {budget}")
            if level == "full-send":
                refs = [r for q in plan["quests"] for s in q["steps"] for r in s.get("item_refs", [])]
                check(f"{label}/full-send covers all tidy items once",
                      sorted(refs) == sorted(i["id"] for i in tidy),
                      f"covered {len(refs)} of {len(tidy)} tidy")
                bosses = [q for q in plan["quests"] if q.get("is_boss")]
                check(f"{label}/full-send has exactly one boss", len(bosses) == 1)
            # Filter: no excluded category ever referenced.
            by_id = {i["id"]: i for i in inv["instances"]}
            bad = [r for q in plan["quests"] for s in q["steps"]
                   for r in s.get("item_refs", []) if by_id[r]["category"] in excluded]
            check(f"{label}/{level} filter holds (no {sorted(excluded)})", not bad, f"refs {bad}")
            # Intro never negative about the mess.
            check(f"{label}/{level} intro is warm",
                  plan["intro_line"] and "mess" not in plan["intro_line"].lower()
                  and "disgusting" not in plan["intro_line"].lower())
            # C6: intro keyed to the actual minutes band, not the level.
            check(f"{label}/{level} intro matches minutes band",
                  plan["intro_line"] == quest_mod._intro_for_minutes(plan["est_minutes"]),
                  f"intro {plan['intro_line']!r} for {plan['est_minutes']} min")
            if level == "full-send":
                last_step = plan["quests"][-1]["steps"][-1]
                check(f"{label}/full-send ends with surface reset",
                      last_step["id"] == "surfaces-s1",
                      f"last step {last_step['id']}")

    # N7: boss tie-break prefers the center zone. Car middle center and
    # middle right tie at 6 tidy items; the boss must be middle center.
    car_inv = json.load(open(CASES[0][1]))
    car_plan = quest_mod.plan(car_inv, "full-send", "vehicle")
    car_boss = [q for q in car_plan["quests"] if q.get("is_boss")]
    check("car/full-send boss is the center zone on a tie",
          len(car_boss) == 1 and car_boss[0]["zone_id"] == "middle center",
          f"boss {[q['zone_id'] for q in car_boss]}")

    # C8: trash sorts first and gets a real throw-out action.
    trash_inv = {
        "image_id": "synthetic_room.jpg",
        "zones": [{"zone_id": "upper center", "label": "upper center",
                   "item_count": 4, "coverage_pct": 10.0}],
        "instances": [
            {"id": "b1", "category": "book", "zone_id": "upper center", "confidence": 0.9},
            {"id": "t1", "category": "trash", "zone_id": "upper center", "confidence": 0.9},
            {"id": "t2", "category": "trash", "zone_id": "upper center", "confidence": 0.9},
            {"id": "p1", "category": "paper", "zone_id": "upper center", "confidence": 0.9},
        ],
    }
    trash_plan = quest_mod.plan(trash_inv, "full-send", "room")
    first_steps = [s for q in trash_plan["quests"] for s in q["steps"]]
    check("trash step sorts first",
          first_steps and first_steps[0]["instruction"].startswith("Throw out"),
          f"first step {first_steps[0]['instruction'] if first_steps else None!r}")
    check("trash instruction is a throw-out action, never 'trashs'",
          first_steps and "bags of trash" in first_steps[0]["instruction"]
          and "trashs" not in first_steps[0]["instruction"],
          f"{first_steps[0]['instruction'] if first_steps else None!r}")

    # Reroute: skip a boss step and a normal step from the car full-send plan.
    inv = json.load(open(CASES[0][1]))
    plan = quest_mod.plan(inv, "full-send", "vehicle")
    boss_quest = next(q for q in plan["quests"] if q.get("is_boss"))
    skip = [boss_quest["steps"][0]["id"], plan["quests"][0]["steps"][0]["id"]]
    replan = quest_mod.reroute(plan, skip)
    tmp = "/tmp/stage2_test_reroute.json"
    json.dump(replan, open(tmp, "w"), indent=2)
    errs = validate_quest.check(tmp, CASES[0][1], "vehicle", rerouted=True)
    check("reroute validates", not errs, "; ".join(errs))
    # C6: rerouted plans recompute the intro from the new minutes band.
    check("reroute recomputes intro from new minutes",
          replan["intro_line"] == quest_mod._intro_for_minutes(replan["est_minutes"]),
          f"intro {replan['intro_line']!r} for {replan['est_minutes']} min")
    remaining = [s["id"] for q in replan["quests"] for s in q["steps"]]
    check("reroute removed skipped steps", all(s not in remaining for s in skip))
    import math as _math
    total = sum(s["est_seconds"] for q in replan["quests"] for s in q["steps"])
    check("reroute recomputed est_minutes",
          replan["est_minutes"] == max(1, _math.ceil(total / 60)))
    # Skip everything -> rest plan, still valid, still kind.
    all_ids = [s["id"] for q in plan["quests"] for s in q["steps"]]
    rest = quest_mod.reroute(plan, all_ids)
    check("skip-all becomes a rest plan",
          len(rest["quests"]) == 1 and len(rest["quests"][0]["steps"]) == 1
          and "Rest first" in rest["quests"][0]["steps"][0]["instruction"])
    json.dump(rest, open("/tmp/stage2_test_rest.json", "w"), indent=2)
    errs = validate_quest.check("/tmp/stage2_test_rest.json", CASES[0][1], "vehicle", rerouted=True)
    check("rest plan validates", not errs, "; ".join(errs))

    # Spark integration point: LIVE since C4, but refuses without a
    # BYOK key (no key, no call -- the credits guardrail is now a key
    # guardrail).
    try:
        spark_mod.plan_with_spark(inv, "an-hour", 3600)
        check("spark plan_with_spark refuses without api_key", False, "it called?!")
    except ValueError as exc:
        check("spark plan_with_spark refuses without api_key",
              "api_key" in str(exc), str(exc))
    # C3: verified model ids are supplied (no longer guessed), and the
    # request uses the json_object response_format shape.
    req = spark_mod.build_request(inv, "an-hour", 3600)
    check("spark build_request uses verified default model",
          req["model"] == "muse-spark-1.2", f"model {req['model']!r}")
    check("spark build_request uses json_object response_format",
          req["response_format"] == {"type": "json_object"},
          f"response_format {req['response_format']!r}")
    req13 = spark_mod.build_request(inv, "an-hour", 3600, model="muse-spark-1.3")
    check("spark build_request accepts muse-spark-1.3",
          req13["model"] == "muse-spark-1.3")
    # C3: parse_response unwraps the chat-completions envelope.
    mini_plan = {
        "quest_title": "Test Reclaim", "energy_level": "an-hour",
        "est_minutes": 5,
        "quests": [{"zone_id": "upper center", "title": "Upper Center Quest",
                    "steps": [{"id": "s1", "instruction": "Do a thing",
                              "est_seconds": 50}]}],
    }
    envelope = {"choices": [{"message": {"content": json.dumps(mini_plan)}}]}
    unwrapped = spark_mod.parse_response(envelope)
    check("spark parse_response unwraps choices[0].message.content",
          unwrapped == mini_plan, f"got {unwrapped}")
    check("spark parse_response still accepts a direct plan dict",
          spark_mod.parse_response(mini_plan) == mini_plan)
    prompt = spark_mod.build_planner_prompt(inv, "an-hour", 3600)
    check("spark prompt embeds inventory + budget",
          "test_01_car_backseat_BEFORE.jpg" in prompt and "3600" in prompt)

    print()
    print(f"{'ALL STAGE 2 CHECKS PASSED' if not FAILURES else str(len(FAILURES)) + ' FAILURES'}")
    return 0 if not FAILURES else 1


if __name__ == "__main__":
    raise SystemExit(main())
