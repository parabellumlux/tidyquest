#!/usr/bin/env python3
"""TidyQuest stage-1 labeling: SAM mask crops -> category + confidence tier.

Primary backend: a LOCAL VLM via ollama (moondream) on luci. No external API
calls, no Meta Model API calls, $0. moondream returns no calibrated score, so
VLM labels carry confidence 0.7 as a DOCUMENTED PLACEHOLDER TIER (labeled by
vision model), not a measurement. When the VLM is unavailable, the transparent
heuristic pass below runs instead and every label is marked provisional with
confidence 0.3. "unknown" is always a valid, honest answer.

Category vocabulary is intentionally small and quest-shaped: the Questmaster
plans trash-first, then categories, surfaces last, so labels must map to
physical sorting actions, not COCO trivia.
"""
import base64
import io
import json
import urllib.request

CATEGORIES = [
    "clothing", "bottle", "paper", "cardboard box", "bag", "shoe", "toy",
    "book", "bedding", "towel", "food container", "electronic", "cable",
    "furniture", "trash", "toiletry", "cleaning supply", "small item",
    "structure", "unknown",
]

# Placeholder tiers (NOT measurements). See module docstring.
CONF_VLM = 0.7
CONF_HEURISTIC = 0.3
CONF_UNKNOWN = 0.3

# Description -> category keyword map, checked in order. Moondream (small
# VLM) degenerates on constrained one-word prompts (observed: it returns
# 'urn' or empty), but describes freely and accurately. So we ask it to
# describe, then map the description to the quest vocabulary here. Specific
# phrases come before generic words ("food container" before "container").
# Object keywords come before structure keywords: a clutter crop described
# as "a car interior with a blanket and a bag" must land on the object, not
# the scene. Pure-scene descriptions fall through to "structure".
_KEYWORD_MAP = [
    ("food container", "food container"),
    ("tupperware", "food container"),
    ("cardboard box", "cardboard box"),
    ("plastic bag", "bag"),
    ("shopping bag", "bag"),
    ("water bottle", "bottle"),
    ("spray bottle", "cleaning supply"),
    ("stuffed animal", "toy"),
    ("toothbrush", "toiletry"),
    ("shampoo", "toiletry"),
    ("cleaning", "cleaning supply"),
    ("bottle", "bottle"),
    ("blanket", "bedding"),
    ("pillow", "bedding"),
    ("bedding", "bedding"),
    ("towel", "towel"),
    ("shirt", "clothing"),
    ("jacket", "clothing"),
    ("clothing", "clothing"),
    ("clothes", "clothing"),
    ("pants", "clothing"),
    ("shoe", "shoe"),
    ("sneaker", "shoe"),
    ("boot", "shoe"),
    ("toy", "toy"),
    ("doll", "toy"),
    ("book", "book"),
    ("notebook", "book"),
    ("newspaper", "paper"),
    ("envelope", "paper"),
    ("receipt", "paper"),
    ("mail", "paper"),
    ("paper", "paper"),
    ("papers", "paper"),
    ("box", "cardboard box"),
    ("bag", "bag"),
    ("container", "food container"),
    ("phone", "electronic"),
    ("laptop", "electronic"),
    ("electronic", "electronic"),
    ("charger", "electronic"),
    ("cable", "cable"),
    ("cord", "cable"),
    ("wire", "cable"),
    ("couch", "furniture"),
    ("chair", "furniture"),
    ("seat", "furniture"),
    ("furniture", "furniture"),
    ("bed", "furniture"),
    ("trash", "trash"),
    ("garbage", "trash"),
    ("roof", "structure"),
    ("ceiling", "structure"),
    ("wall", "structure"),
    ("window", "structure"),
    ("dashboard", "structure"),
    ("door", "structure"),
    ("car interior", "structure"),
    ("interior of a car", "structure"),
]


def _map_description(text: str) -> str:
    t = text.strip().lower()
    for phrase, cat in _KEYWORD_MAP:
        if phrase in t:
            return cat
    return "unknown"


def crop_for_instance(image, inst, margin=0.12):
    x0, y0, x1, y1 = inst["bbox_xyxy"]
    W, H = image.size
    w, h = x1 - x0, y1 - y0
    x0 = max(0, int(x0 - w * margin))
    y0 = max(0, int(y0 - h * margin))
    x1 = min(W, int(x1 + w * margin))
    y1 = min(H, int(y1 + h * margin))
    return image.crop((x0, y0, x1, y1))


def _ollama_generate(image_b64: str, model: str, timeout: int = 120) -> str:
    # NOTE: moondream degenerates on constrained one-word prompts (observed
    # outputs: 'urn', ''). It describes freely and accurately, so we ask for
    # a description and map it to the category vocabulary in _map_description.
    prompt = "Describe this image briefly."
    body = json.dumps({
        "model": model, "prompt": prompt, "images": [image_b64],
        "stream": False, "options": {"temperature": 0},
    }).encode()
    req = urllib.request.Request(
        "http://localhost:11434/api/generate", data=body,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode()).get("response", "")


def _parse_category(text: str) -> str:
    return _map_description(text)


def label_with_vlm(image, inst, model="moondream"):
    """Label one instance with the local VLM. Raises on VLM failure so the
    caller can fall back to the heuristic pass (and say so)."""
    crop = crop_for_instance(image, inst)
    buf = io.BytesIO()
    crop.save(buf, format="PNG")
    text = _ollama_generate(base64.b64encode(buf.getvalue()).decode(), model)
    cat = _map_description(text)
    if cat == "unknown":
        return cat, CONF_UNKNOWN, "vlm:moondream"
    return cat, CONF_VLM, "vlm:moondream"


def label_heuristic(image, inst):
    """Transparent provisional rules (geometry + brightness only). Documented
    as weak on purpose: this pass exists so the pipeline still produces
    category counts when the VLM is down, not because these rules are good.
    - Big solid mass in the lower half -> furniture (bed/couch/seat).
    - Very long thin mask -> cable.
    - Tiny mask -> small item.
    - Bright small flat mask -> paper (weak; white-ish clutter).
    - Everything else -> unknown.
    """
    x0, y0, x1, y1 = inst["bbox_xyxy"]
    W, H = image.size
    w, h = max(1, x1 - x0), max(1, y1 - y0)
    aspect = max(w, h) / min(w, h)
    area = inst["area_pct"]
    cy = inst["centroid"][1]
    solidity = (area / 100.0) / ((w * h) / float(W * H)) if w * h else 0.0
    crop = crop_for_instance(image, inst)
    import numpy as np
    mean_bright = float(np.array(crop.convert("L")).mean())
    if area >= 12.0 and cy >= 0.5 and solidity >= 0.5:
        return "furniture", CONF_HEURISTIC, "heuristic:provisional"
    if aspect >= 4.0 and area < 2.0:
        return "cable", CONF_HEURISTIC, "heuristic:provisional"
    if area < 0.25:
        return "small item", CONF_HEURISTIC, "heuristic:provisional"
    if mean_bright > 200 and 0.3 <= area <= 4.0:
        return "paper", CONF_HEURISTIC, "heuristic:provisional"
    return "unknown", CONF_UNKNOWN, "heuristic:provisional"
