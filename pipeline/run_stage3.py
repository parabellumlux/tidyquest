#!/usr/bin/env python3
"""TidyQuest stage 3 runner: compose the two sample share cards.

- Car backseat: proof_mode="differ" -- the differ proof (tidy-only
  cleared count from the differ's category deltas + plan minutes).
- Donna's bathroom: proof_mode="quest" -- NO reduction claim (the
  differ does not yield one for this angle-mismatched pair); the card
  shows plan items + plan minutes plus the angle caveat, printed on
  the card itself.

No network calls. No credentials. Writes cards + sidecar JSONs to out/.
Run from the build root: .venv/bin/python pipeline/run_stage3.py
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from card import compose_card  # noqa: E402


def _load(path):
    with open(path) as f:
        return json.load(f)


def main():
    out_dir = os.path.join(ROOT, "out")
    jobs = [
        {
            "name": "car",
            "before": os.path.join(ROOT, "test_01_car_backseat_BEFORE.jpg"),
            "after": os.path.join(ROOT, "test_01_car_backseat_AFTER.jpg"),
            "plan": _load(os.path.join(out_dir, "stage2_car_quest.json")),
            "differ": _load(os.path.join(out_dir, "stage1_car_differ.json")),
            "proof_mode": "differ",
            "card": os.path.join(out_dir, "stage3_car_card.png"),
            "sidecar": os.path.join(out_dir, "stage3_car_card.json"),
        },
        {
            "name": "donna",
            "before": os.path.join(ROOT, "test_05_donna_bathroom_BEFORE.jpg"),
            "after": os.path.join(ROOT, "test_05_donna_bathroom_AFTER_wideangle.jpg"),
            "plan": _load(os.path.join(out_dir, "stage2_donna_quest.json")),
            "differ": _load(os.path.join(out_dir, "stage1_donna_differ.json")),
            "proof_mode": "quest",
            "card": os.path.join(out_dir, "stage3_donna_card.png"),
            "sidecar": os.path.join(out_dir, "stage3_donna_card.json"),
        },
    ]
    for job in jobs:
        path, sidecar = compose_card(
            job["before"],
            job["after"],
            job["plan"],
            differ=job["differ"],
            proof_mode=job["proof_mode"],
            after_kind="photo",
            out_path=job["card"],
            sidecar_path=job["sidecar"],
        )
        print(f"[{job['name']}] card -> {path}")
        print(f"[{job['name']}] stats: {sidecar['stats_text']}")
        print(f"[{job['name']}] sidecar -> {job['sidecar']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
