#!/usr/bin/env python3
"""End-to-end proxy test: plan, reroute, differ, card on sample data."""
import base64
import json
import urllib.request

BASE = "http://127.0.0.1:8001"
BUILD = "/home/damon/tidyquest-build"


def call(path, body):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.load(r)


def load(p):
    with open(p) as f:
        return json.load(f)


def load_b64(p):
    with open(p, "rb") as f:
        return base64.b64encode(f.read()).decode()


inv_before = load(f"{BUILD}/out/stage1_car_before_vlm/inventory.json")
inv_after = load(f"{BUILD}/out/stage1_car_after_vlm/inventory.json")

plan = call("/plan", {"inventory": inv_before, "energy_level": "an-hour",
                       "space_type": "vehicle"})["plan"]
print("PLAN:", plan["quest_title"], plan["est_minutes"], "min,",
      len(plan["quests"]), "quests,",
      sum(len(q["steps"]) for q in plan["quests"]), "steps")
first_step = plan["quests"][0]["steps"][0]["id"]
plan2 = call("/reroute", {"plan": plan,
                           "skipped_step_ids": [first_step]})["plan"]
steps2 = sum(len(q["steps"]) for q in plan2["quests"])
print("REROUTE: dropped", first_step, "->", steps2, "steps,",
      plan2["est_minutes"], "min")

differ = call("/differ", {"before_inventory": inv_before,
                           "after_inventory": inv_after,
                           "angle_mismatch": True,
                           "space_type": "vehicle"})["differ"]
print("DIFFER totals:", differ["totals"])

card = call("/card", {
    "before_b64": load_b64(f"{BUILD}/test_01_car_backseat_BEFORE.jpg"),
    "before_name": "test_01_car_backseat_BEFORE.jpg",
    "after_b64": load_b64(f"{BUILD}/test_01_car_backseat_AFTER.jpg"),
    "after_name": "test_01_car_backseat_AFTER.jpg",
    "plan": plan,
    "differ": differ,
    "before_image_id": inv_before["image_id"],
    "space_type": "vehicle",
})
print("CARD proof_mode:", card["proof_mode"],
      "| stats:", card["sidecar"]["stats_text"],
      "| card bytes:", len(card["card_b64"]))
print("CARD privacy before:", card["sidecar"]["privacy"]["before"])
print("ALL PROXY LIVE-ENDPOINT TESTS PASSED")
