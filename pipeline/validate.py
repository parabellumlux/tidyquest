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
    """Return a list of validation errors (never raise on malformed input).

    A malformed inventory -- missing keys, wrong types, a non-object
    document -- is reported as errors, never a KeyError crash.
    """
    try:
        d = json.load(open(path))
    except (json.JSONDecodeError, OSError) as exc:
        return [f"unreadable inventory: {exc}"]
    if not isinstance(d, dict):
        return ["inventory is not a JSON object"]
    errs = []
    for k in REQUIRED_TOP:
        if k not in d:
            errs.append(f"missing top-level {k}")
    instances = d.get("instances")
    zones = d.get("zones")
    if isinstance(instances, list) and "total_items" in d:
        if d["total_items"] != len(instances):
            errs.append(f"total_items {d['total_items']} != len(instances) {len(instances)}")
    if isinstance(instances, list):
        if Counter(i.get("category") for i in instances if isinstance(i, dict)) != Counter(d.get("category_counts", {})):
            errs.append("top category_counts mismatch")
    elif "instances" in d:
        errs.append("instances is not a list")
    by_zone = {}
    if isinstance(instances, list):
        for i in instances:
            if isinstance(i, dict):
                by_zone.setdefault(i.get("zone_id"), []).append(i)
    if isinstance(zones, list):
        for z in zones:
            if not isinstance(z, dict):
                errs.append("zone entry is not an object")
                continue
            for k in REQUIRED_ZONE:
                if k not in z:
                    errs.append(f"zone {z.get('zone_id')} missing {k}")
            zi = by_zone.get(z.get("zone_id"), [])
            if "item_count" in z and z["item_count"] != len(zi):
                errs.append(f"zone {z['zone_id']} count {z['item_count']} != {len(zi)}")
            if Counter(i.get("category") for i in zi) != Counter(z.get("category_counts", {})):
                errs.append(f"zone {z['zone_id']} category_counts mismatch")
    elif "zones" in d:
        errs.append("zones is not a list")
    if isinstance(instances, list):
        for i in instances:
            if not isinstance(i, dict):
                errs.append("instance entry is not an object")
                continue
            for k in REQUIRED_INST:
                if k not in i:
                    errs.append(f"instance {i.get('id')} missing {k}")
            conf = i.get("confidence")
            if conf is not None and not (0 <= conf <= 1):
                errs.append(f"instance {i.get('id')} confidence out of range")
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
