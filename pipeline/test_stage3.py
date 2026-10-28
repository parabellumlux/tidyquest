#!/usr/bin/env python3
"""TidyQuest stage 3 checks. Run: .venv/bin/python pipeline/test_stage3.py

Covers: mirror refusal, blur actually changing pixels (and only inside
the box), honest numbers (car differ proof vs Donna quest-only + caveat),
and the Image integration point live (BYOK key required).
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from PIL import Image

import card as card_mod
import image as image_mod
import privacy as privacy_mod


def _tiny_image():
    # Checkerboard inside the box: blur MUST change the center pixel
    # (a uniform patch would survive a Gaussian blur unchanged).
    img = Image.new("RGB", (100, 100), (200, 200, 200))
    px = img.load()
    for x in range(40, 60):
        for y in range(40, 60):
            px[x, y] = (10, 20, 30) if (x + y) % 2 == 0 else (250, 240, 230)
    return img


def test_blur_changes_only_the_box():
    img = _tiny_image()
    before_center = img.getpixel((50, 50))
    out = privacy_mod.blur_box(img, (40, 40, 60, 60), radius=5)
    assert out.getpixel((50, 50)) != before_center, "blur must change the box"
    assert out.getpixel((0, 0)) == (200, 200, 200), "outside must be untouched"
    assert img.getpixel((50, 50)) == before_center, "input must not mutate"


def test_mirror_refusal():
    img = _tiny_image()
    saved = dict(privacy_mod.MIRROR_REGISTRY)
    try:
        privacy_mod.MIRROR_REGISTRY.pop("test_05_donna_bathroom_BEFORE.jpg", None)
        # Keep it in the known set but unmarked: must refuse.
        privacy_mod.KNOWN_MIRROR_PHOTOS = frozenset(
            set(privacy_mod.KNOWN_MIRROR_PHOTOS)
            | {"test_05_donna_bathroom_BEFORE.jpg"}
        )
        try:
            privacy_mod.apply_privacy(img, "test_05_donna_bathroom_BEFORE.jpg")
        except ValueError as exc:
            assert "unmarked" in str(exc)
        else:
            raise AssertionError("unmarked mirror photo must refuse")
    finally:
        privacy_mod.MIRROR_REGISTRY.clear()
        privacy_mod.MIRROR_REGISTRY.update(saved)


def test_tidy_cleared_car():
    with open(os.path.join(ROOT, "out", "stage1_car_differ.json")) as f:
        differ = json.load(f)
    cleared, raw_before, raw_after = card_mod.tidy_cleared(differ, "vehicle")
    assert (raw_before, raw_after) == (34, 11)
    assert cleared == 26, f"tidy cleared should be 26, got {cleared}"


def test_stats_modes():
    car_plan = {"quest_title": "Car Backseat Reclaim", "est_minutes": 22}
    donna_plan = {"quest_title": "Donna's Bathroom Reclaim", "est_minutes": 18}
    with open(os.path.join(ROOT, "out", "stage1_car_differ.json")) as f:
        car_differ = json.load(f)
    with open(os.path.join(ROOT, "out", "stage1_donna_differ.json")) as f:
        donna_differ = json.load(f)
    # Donna plan item count: sum item_refs over the sample plan.
    with open(os.path.join(ROOT, "out", "stage2_donna_quest.json")) as f:
        donna_plan_full = json.load(f)
    text, meta = card_mod.stats_for_card(car_plan, car_differ, "differ",
                                         space_type="vehicle")
    assert text == "26 items · 22 minutes · 1 space reclaimed", text
    assert meta["reduction_claim"] is True
    text, meta = card_mod.stats_for_card(donna_plan_full, donna_differ, "quest",
                                         space_type="room")
    assert text == "23 items · 18 minutes · 1 space reclaimed", text
    assert meta["reduction_claim"] is False
    assert meta["caveat"] is not None
    assert "cleared" not in text.lower()
    # Differ mode must refuse a negative tidy cleared count.
    bad = {"before_image": "test_05_donna_bathroom_BEFORE.jpg",
           "category_deltas": {"clothing": 3}, "totals": {}}
    try:
        card_mod.stats_for_card(donna_plan, bad, "differ", space_type="room")
    except ValueError:
        pass
    else:
        raise AssertionError("negative tidy cleared must refuse differ mode")


def test_image_enabled():
    # LIVE since C4 (2026-10-27): IMAGE_ENABLED is True, building with
    # the default model still succeeds, and generation without a BYOK
    # key refuses with a key error (not the old credits refusal).
    req = image_mod.build_after_vision_request("ref.jpg")
    assert req["model"] == "muse-image-1.0", req
    assert image_mod.IMAGE_ENABLED is True
    try:
        image_mod.generate_after_vision("ref.jpg")
    except ValueError as exc:
        assert "api_key" in str(exc)
    else:
        raise AssertionError("Image generation must require api_key")


def main():
    tests = [
        test_blur_changes_only_the_box,
        test_mirror_refusal,
        test_tidy_cleared_car,
        test_stats_modes,
        test_image_enabled,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"ALL STAGE 3 CHECKS PASSED ({len(tests)} tests)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
