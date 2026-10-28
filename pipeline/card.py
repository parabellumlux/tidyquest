#!/usr/bin/env python3
"""TidyQuest stage 3 -- share-card composer (no live calls).

Takes a before photo, an after photo (a real re-scan photo today; a Muse
Image after-vision render once that backend is live), a quest plan, and
a differ output, and composes the shareable before/after card:
before/after split, quest stats in the plan's card_text format, #TidyQuest.

Privacy FIRST: both photos pass through privacy.apply_privacy() before
anything is composited. A photo known to contain mirrors but missing its
registry entry refuses composition outright -- no card is written.

HONEST NUMBERS (stage-1 note, enforced here, not just documented):
- proof_mode="differ" (the car pair): the card's item count is the
  TIDY-ONLY cleared count from the differ's category deltas (structure
  always excluded; furniture excluded in vehicle interiors). Raw totals
  ride along in the sidecar, never as the headline. This is the demo's
  progress proof.
- proof_mode="quest" (Donna's bathroom): the differ does NOT yield a
  clean reduction (27 -> 29, angle mismatch, wider AFTER framing), so
  the card NEVER presents a reduction for this pair. Its item count is
  the tidy items in the quest plan, its minutes are the plan estimate,
  and a caveat line is printed on the card itself: different camera
  angles, counts reflect the quest plan, not a matched before/after
  count. The sidecar records reduction_claim=False.

The plan's card_text template is "{items} items · {minutes} minutes ·
1 space reclaimed"; this module FILLS that template with the proof-mode
numbers (differ: tidy cleared items; quest: plan items) -- it never
shows literal braces, and it never invents a different format. If a
plan carries no template (older plans), the same format string is used
as the fallback.
"""

import json
import os

from PIL import Image, ImageDraw, ImageFont

try:
    from privacy import apply_privacy
except ImportError:  # pragma: no cover - direct script execution
    from pipeline.privacy import apply_privacy

HALF_HEIGHT = 1000
BANNER_HEIGHT = 230
DIVIDER_PX = 4
LABEL_PAD = 16
CAVEAT_TEXT = (
    "Different camera angles -- counts reflect the quest plan, "
    "not a matched before/after count."
)

_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
    "/usr/share/fonts/noto/NotoSans-Bold.ttf",
    "/usr/share/fonts/noto/NotoSans-Regular.ttf",
]


def _load_font(size, bold=False):
    """Best available TTF font, falling back to PIL's default bitmap."""
    for path in _FONT_CANDIDATES:
        if bold and "Bold" not in path:
            continue
        if not bold and "Bold" in path:
            continue
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _excluded_categories(space_type):
    """Mirror of quest.py's filter (authority: pipeline/quest.py)."""
    excluded = {"structure"}
    if space_type == "vehicle":
        excluded |= {"furniture"}
    return excluded


def _format_card_text(plan, items, minutes):
    """Fill the plan's card_text template; fall back to the same format
    string when the plan carries no usable template. Never returns
    literal braces."""
    template = None
    try:
        template = plan.get("completion", {}).get("card_text")
    except AttributeError:
        template = None
    if not isinstance(template, str) or "{items}" not in template or "{minutes}" not in template:
        template = "{items} items · {minutes} minutes · 1 space reclaimed"
    try:
        return template.format(items=items, minutes=minutes)
    except (KeyError, IndexError, ValueError):
        return f"{items} items · {minutes} minutes · 1 space reclaimed"


def tidy_cleared(differ, space_type):
    """Tidy-only items cleared, from the differ's category deltas.

    Raw differ totals include structure (and vehicle furniture) that was
    never tidy-able; counting it would inflate or distort the proof.
    Returns (cleared, raw_before, raw_after).
    """
    excluded = _excluded_categories(space_type)
    delta = 0
    for cat, d in (differ.get("category_deltas") or {}).items():
        if cat not in excluded:
            delta += d
    totals = differ.get("totals") or {}
    return -delta, totals.get("before_total"), totals.get("after_total")


def plan_tidy_items(plan):
    """Tidy items covered by a quest plan (sum of item_refs)."""
    total = 0
    for quest in plan.get("quests", []):
        for step in quest.get("steps", []):
            total += len(step.get("item_refs", []))
    return total


def stats_for_card(plan, differ, proof_mode, space_type="room"):
    """Compute the card's stats line + honesty metadata.

    Returns (stats_text, meta). proof_mode "differ" requires a differ
    dict; "quest" forbids any reduction claim. space_type is explicit
    ("room" default; "vehicle" for vehicle interiors) -- never inferred
    from a filename. stats_text is the plan's card_text template FILLED
    with the proof-mode numbers.
    """
    minutes = plan.get("est_minutes")
    if minutes is None:
        raise ValueError("quest plan has no est_minutes")
    if space_type not in ("room", "vehicle"):
        raise ValueError(
            f"unknown space_type {space_type!r}; expected 'room' or 'vehicle'"
        )
    if proof_mode == "differ":
        if differ is None:
            raise ValueError("proof_mode 'differ' requires a differ output")
        cleared, raw_before, raw_after = tidy_cleared(differ, space_type)
        if cleared < 0:
            raise ValueError(
                "differ shows a negative tidy cleared count; refusing to "
                "present it as a reduction -- use proof_mode 'quest'"
            )
        text = _format_card_text(plan, cleared, minutes)
        meta = {
            "proof_mode": "differ",
            "reduction_claim": True,
            "items_cleared_tidy": cleared,
            "raw_before_total": raw_before,
            "raw_after_total": raw_after,
            "caveat": None,
        }
        return text, meta
    if proof_mode == "quest":
        items = plan_tidy_items(plan)
        text = _format_card_text(plan, items, minutes)
        meta = {
            "proof_mode": "quest",
            "reduction_claim": False,
            "items_in_plan": items,
            "caveat": CAVEAT_TEXT,
        }
        if differ is not None:
            totals = differ.get("totals") or {}
            meta["differ_totals_not_shown"] = {
                "before_total": totals.get("before_total"),
                "after_total": totals.get("after_total"),
                "cleared": totals.get("cleared"),
                "angle_mismatch": differ.get("angle_mismatch"),
            }
        return text, meta
    raise ValueError(f"unknown proof_mode {proof_mode!r}")


def _scaled_half(image, height=HALF_HEIGHT):
    """Scale one photo to the card's half height, preserving aspect."""
    w, h = image.size
    new_w = max(1, round(w * height / h))
    return image.resize((new_w, height), Image.LANCZOS)


def _draw_label(draw, text, x, y):
    """A small filled label (BEFORE / AFTER) in the corner of a half.

    Uses anchor "lt" for both measurement and drawing so the black box
    exactly contains the glyphs (the default "la" anchor under-measures
    and lets text bleed past the box bottom).
    """
    font = _load_font(34, bold=True)
    box = draw.textbbox((0, 0), text, font=font, anchor="lt")
    tw, th = box[2] - box[0], box[3] - box[1]
    pad = 10
    draw.rectangle(
        [x, y, x + tw + pad * 2, y + th + pad * 2], fill=(20, 20, 20)
    )
    draw.text((x + pad, y + pad), text, font=font, fill=(255, 255, 255),
              anchor="lt")


def compose_card(before_path, after_path, plan, differ=None,
                 proof_mode="differ", after_kind="photo",
                 out_path=None, sidecar_path=None, space_type="room"):
    """Compose the share card. Privacy runs before any pixel is composited.

    after_kind: "photo" (real re-scan) or "vision" (Muse Image render).
    The right half is labeled AFTER or AFTER-VISION accordingly.
    space_type is explicit ("room" default; "vehicle" for vehicle
    interiors) -- never inferred from a filename.

    Returns (card_path, sidecar_dict). Raises ValueError on an unmarked
    mirror photo, a missing plan field, or a dishonest number request.
    """
    if not plan.get("quest_title"):
        raise ValueError("quest plan has no quest_title")
    stats_text, meta = stats_for_card(plan, differ, proof_mode, space_type)

    before = Image.open(before_path).convert("RGB")
    after = Image.open(after_path).convert("RGB")
    before, before_report = apply_privacy(before, before_path)
    after, after_report = apply_privacy(after, after_path)

    left = _scaled_half(before)
    right = _scaled_half(after)
    width = left.width + DIVIDER_PX + right.width
    height = HALF_HEIGHT + BANNER_HEIGHT
    card = Image.new("RGB", (width, height), (255, 255, 255))
    card.paste(left, (0, 0))
    card.paste(right, (left.width + DIVIDER_PX, 0))
    draw = ImageDraw.Draw(card)
    draw.rectangle(
        [left.width, 0, left.width + DIVIDER_PX - 1, HALF_HEIGHT - 1],
        fill=(20, 20, 20),
    )
    _draw_label(draw, "BEFORE", LABEL_PAD, LABEL_PAD)
    _draw_label(
        draw,
        "AFTER-VISION" if after_kind == "vision" else "AFTER",
        left.width + DIVIDER_PX + LABEL_PAD,
        LABEL_PAD,
    )

    # Banner: quest title, stats, optional caveat, tag.
    y = HALF_HEIGHT + 24
    title_font = _load_font(46, bold=True)
    draw.text((LABEL_PAD, y), plan["quest_title"], font=title_font, fill=(20, 20, 20))
    y += 62
    stats_font = _load_font(40, bold=False)
    draw.text((LABEL_PAD, y), stats_text, font=stats_font, fill=(60, 60, 60))
    y += 56
    if meta.get("caveat"):
        caveat_font = _load_font(26, bold=False)
        draw.text((LABEL_PAD, y), meta["caveat"], font=caveat_font, fill=(120, 60, 60))
        y += 40
    tag_font = _load_font(28, bold=True)
    tag = "#TidyQuest"
    tag_box = draw.textbbox((0, 0), tag, font=tag_font)
    tag_w = tag_box[2] - tag_box[0]
    draw.text((width - tag_w - LABEL_PAD, HALF_HEIGHT + 24), tag,
              font=tag_font, fill=(90, 90, 180))

    if out_path is None:
        out_path = "share_card.png"
    card.save(out_path)
    sidecar = {
        "card_path": out_path,
        "quest_title": plan["quest_title"],
        "stats_text": stats_text,
        "before_photo": os.path.basename(before_path),
        "after_photo": os.path.basename(after_path),
        "after_kind": after_kind,
        "privacy": {"before": before_report, "after": after_report},
        **meta,
    }
    if sidecar_path:
        with open(sidecar_path, "w") as f:
            json.dump(sidecar, f, indent=2)
    return out_path, sidecar
