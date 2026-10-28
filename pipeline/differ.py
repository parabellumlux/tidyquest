#!/usr/bin/env python3
"""TidyQuest zone-aggregate before/after differ.

Compares two inventory.json files ONLY by per-zone totals (counts, coverage,
per-category counts). It NEVER matches instances across photos: instance ids
are unstable across scans, and different-angle pairs (Donna's bathroom) share
no viewpoint at all. Zone totals are the contract; per-instance matching is
explicitly out of scope.

Output (differ.json):
  before/after image ids, per-zone {before_count, after_count, delta,
  delta_pct, coverage_before/after/delta, category_deltas}, totals
  {before_total, after_total, cleared, cleared_pct} (RAW totals --
  secondary, framing-sensitive), headline {cleared, cleared_pct, basis}
  (TIDY-ONLY cleared: the quest filter -- `structure` always excluded,
  `furniture` excluded in vehicle interiors -- applied to both sides;
  this is the number a card or demo may headline), category totals
  deltas, and a notes list (e.g. the different-angle caveat).
"""
import json
import sys
from collections import Counter


def excluded_categories(space_type):
    """Mirror of quest.py's filter (authority: pipeline/quest.py)."""
    excluded = {"structure"}
    if space_type == "vehicle":
        excluded |= {"furniture"}
    return excluded


def _zone_map(inv):
    zones = {}
    for z in inv["zones"]:
        zones[z["zone_id"]] = z
    return zones


def _cat_counts(inv):
    c = Counter()
    for inst in inv["instances"]:
        c[inst["category"]] += 1
    return c


def diff(before, after, angle_mismatch=False, space_type="room"):
    """Zone-aggregate differ. space_type is passed explicitly by the
    caller ("room" default; "vehicle" for vehicle interiors) -- it is
    never inferred from a filename here. The headline cleared count is
    TIDY-ONLY (quest filter on both sides); raw totals are secondary
    and, under angle_mismatch, explicitly NOT reliable across framings.
    """
    zb, za = _zone_map(before), _zone_map(after)
    zones = {}
    for zid in sorted(set(zb) | set(za)):
        b = zb.get(zid, {"item_count": 0, "coverage_pct": 0.0})
        a = za.get(zid, {"item_count": 0, "coverage_pct": 0.0})
        bc = Counter(b.get("category_counts", {}))
        ac = Counter(a.get("category_counts", {}))
        cat_deltas = {c: ac.get(c, 0) - bc.get(c, 0)
                      for c in sorted(set(bc) | set(ac))
                      if ac.get(c, 0) - bc.get(c, 0) != 0}
        delta = a["item_count"] - b["item_count"]
        zones[zid] = {
            "before_count": b["item_count"],
            "after_count": a["item_count"],
            "delta": delta,
            "delta_pct": round(100.0 * delta / b["item_count"], 1) if b["item_count"] else None,
            "coverage_before": b["coverage_pct"],
            "coverage_after": a["coverage_pct"],
            "coverage_delta": round(a["coverage_pct"] - b["coverage_pct"], 2),
            "category_deltas": cat_deltas,
        }
    cb, ca = _cat_counts(before), _cat_counts(after)
    cat_totals = {c: ca.get(c, 0) - cb.get(c, 0)
                  for c in sorted(set(cb) | set(ca))
                  if ca.get(c, 0) - cb.get(c, 0) != 0}
    bt, at = before["total_items"], after["total_items"]
    # Headline: tidy-only cleared (quest filter on both sides). Raw
    # totals are secondary: they include structure (and vehicle
    # furniture) that was never tidy-able, and framing changes alone
    # can move them (Donna: raw -2 while tidy cleared +1).
    excluded = excluded_categories(space_type)
    bt_tidy = sum(1 for i in before["instances"] if i["category"] not in excluded)
    at_tidy = sum(1 for i in after["instances"] if i["category"] not in excluded)
    cleared_tidy = bt_tidy - at_tidy
    notes = []
    if angle_mismatch:
        notes.append(
            "Different-angle pair: before and after were shot from different "
            "viewpoints, so per-zone totals are indicative (same 3x3 grid over "
            "different framings), not exact. Raw totals are NOT reliable "
            "across framings -- framing changes alone can move them. The "
            "tidy-category delta is the headline; a reduction is only "
            "claimed when BOTH the raw total and the tidy total fell.")
    return {
        "before_image": before["image_id"],
        "after_image": after["image_id"],
        "model": before.get("model"),
        "angle_mismatch": angle_mismatch,
        "space_type": space_type,
        "zones": zones,
        "totals": {
            "before_total": bt,
            "after_total": at,
            "cleared": bt - at,
            "cleared_pct": round(100.0 * (bt - at) / bt, 1) if bt else None,
        },
        "headline": {
            "cleared": cleared_tidy,
            "cleared_pct": round(100.0 * cleared_tidy / bt_tidy, 1) if bt_tidy else None,
            "before_tidy": bt_tidy,
            "after_tidy": at_tidy,
            "basis": "tidy-only (structure always excluded; furniture excluded in vehicle interiors)",
        },
        "category_deltas": cat_totals,
        "notes": notes,
    }


def main() -> int:
    if len(sys.argv) < 4:
        print("usage: differ.py <before_inv.json> <after_inv.json> <out.json> [--angle-mismatch] [--space-type room|vehicle]",
              file=sys.stderr)
        return 2
    space_type = "room"
    for i, arg in enumerate(sys.argv):
        if arg == "--space-type" and i + 1 < len(sys.argv):
            space_type = sys.argv[i + 1]
    if space_type not in ("room", "vehicle"):
        print(f"unknown --space-type {space_type!r}", file=sys.stderr)
        return 2
    with open(sys.argv[1]) as f:
        before = json.load(f)
    with open(sys.argv[2]) as f:
        after = json.load(f)
    result = diff(before, after, angle_mismatch="--angle-mismatch" in sys.argv,
                  space_type=space_type)
    with open(sys.argv[3], "w") as f:
        json.dump(result, f, indent=2)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
