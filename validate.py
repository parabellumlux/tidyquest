#!/usr/bin/env python3
"""Validate stage-1 inventory.json files against the inventory schema contract:
required fields present, category_counts consistent with instances, zone
counts consistent with instances. (Lightweight check; no jsonschema lib needed.)
"""
import json
import sys
from collections import Counter

REQUIRED_TOP = ["image_id", "model", "total_items", "zones", "instances"]
REQUIRED_INST = ["id", "category", "zone_id", "confidence"]
REQUIRED_ZONE = ["zone_id", "label", "item_count"]


def check(path):
    d = json.load(open(path))
    errs = []
    for k in REQUIRED_TOP:
        if k not in d:
            errs.append(f"missing top-level {k}")
    if d["total_items"] != len(d["instances"]):
        errs.append(f"total_items {d['total_items']} != len(instances) {len(d['instances'])}")
    if Counter(i["category"] for i in d["instances"]) != Counter(d.get("category_counts", {})):
        errs.append("top category_counts mismatch")
    by_zone = {}
    for i in d["instances"]:
        by_zone.setdefault(i["zone_id"], []).append(i)
    for z in d["zones"]:
        for k in REQUIRED_ZONE:
            if k not in z:
                errs.append(f"zone {z.get('zone_id')} missing {k}")
        zi = by_zone.get(z["zone_id"], [])
        if z["item_count"] != len(zi):
            errs.append(f"zone {z['zone_id']} count {z['item_count']} != {len(zi)}")
        if Counter(i["category"] for i in zi) != Counter(z.get("category_counts", {})):
            errs.append(f"zone {z['zone_id']} category_counts mismatch")
    for i in d["instances"]:
        for k in REQUIRED_INST:
            if k not in i:
                errs.append(f"instance {i.get('id')} missing {k}")
        if not (0 <= i["confidence"] <= 1):
            errs.append(f"instance {i['id']} confidence out of range")
    return errs


def main():
    ok = True
    for path in sys.argv[1:]:
        errs = check(path)
        print(("PASS " if not errs else "FAIL ") + path)
        for e in errs:
            print("   ", e)
            ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
