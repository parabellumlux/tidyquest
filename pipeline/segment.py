#!/usr/bin/env python3
"""TidyQuest stage-1 segmentation: SAM masks -> deduped instances + 3x3 zones.

Refactored from spike/segment_spike.py. Differences vs the spike:
- Mask dedup (Baymax spike review): largest-first; a mask is dropped only when
  it is a near-duplicate of a kept mask -- IoU >= 0.5, or >=95% contained in a
  kept mask AND at least half its area. A small real object sitting on a big
  one (phone on a bed) is NOT dropped: its containment is high but its area
  ratio is tiny, so it survives. This targets duplicate masks, not real items.
- Zones stay the spike's 3x3 grid (zone_id like "middle center"). Semantic
  zone clustering is a later stage; the grid keeps the zone-aggregate differ
  stable across before/after pairs, including different-angle pairs.
"""
import numpy as np
from PIL import Image

ZONE_ROWS = ["upper", "middle", "lower"]
ZONE_COLS = ["left", "center", "right"]
MIN_AREA_PCT = 0.10
MAX_INSTANCES = 250
IOU_THRESH = 0.5
CONTAIN_THRESH = 0.95
CONTAIN_AREA_RATIO = 0.5


def zone_of(cx: float, cy: float) -> str:
    col = min(int(cx * 3), 2)
    row = min(int(cy * 3), 2)
    return f"{ZONE_ROWS[row]} {ZONE_COLS[col]}"


def run_sam(image_path: str, model_path: str):
    """Run SAM; return (masks, device_used).

    GPU first. If the GPU pass raises CUDA out-of-memory, the model is
    freed, the CUDA cache is emptied, and the pass is retried on CPU
    rather than failing -- a phone photo must never kill the scan.
    Non-OOM errors propagate unchanged (no silent retry on real bugs).
    """
    import torch

    def _attempt(device):
        from ultralytics import SAM

        model = None
        try:
            model = SAM(model_path)
            results = model(image_path, verbose=False, device=device)
            r = results[0]
            if r.masks is None:
                return []
            img = Image.open(image_path).convert("RGB")
            W, H = img.size
            masks = r.masks.data.cpu().numpy()
            out = []
            for m in masks:
                m = m.astype(bool)
                mh, mw = m.shape
                if (mh, mw) != (H, W):
                    m = np.array(Image.fromarray(m).resize((W, H), Image.NEAREST))
                out.append(m)
            return out
        finally:
            # Release GPU memory before the labeling step: the 4GB card
            # is tight and the local VLM (ollama) needs room.
            if model is not None:
                del model
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

    device = "cuda" if torch.cuda.is_available() else "cpu"
    try:
        return _attempt(device), device
    except Exception as exc:
        if device == "cuda" and "out of memory" in str(exc).lower():
            return _attempt("cpu"), "cpu"
        raise


def dedup_masks(masks):
    """Largest-first dedup. Returns (kept_indices, dropped_indices)."""
    order = sorted(range(len(masks)), key=lambda i: -int(masks[i].sum()))
    kept, dropped = [], []
    for i in order:
        m = masks[i]
        a = int(m.sum())
        drop = False
        if a == 0:
            drop = True
        for k in kept:
            km = masks[k]
            ka = int(km.sum())
            inter = int((m & km).sum())
            union = a + ka - inter
            iou = inter / union if union else 0.0
            containment = inter / min(a, ka) if min(a, ka) else 0.0
            ratio = min(a, ka) / max(a, ka) if max(a, ka) else 0.0
            if iou >= IOU_THRESH or (containment >= CONTAIN_THRESH and ratio >= CONTAIN_AREA_RATIO):
                drop = True
                break
        (dropped if drop else kept).append(i)
    return kept, dropped


def instances_from_masks(masks, kept, frame_w, frame_h):
    """Build schema-shaped instance dicts (category filled in by label.py)."""
    frame_area = float(frame_w * frame_h)
    instances, zone_counts, zone_coverage = [], {}, {}
    for new_id, idx in enumerate(kept, start=1):
        m = masks[idx]
        area = float(m.sum())
        area_pct = 100.0 * area / frame_area
        if area_pct < MIN_AREA_PCT or len(instances) >= MAX_INSTANCES:
            continue
        ys, xs = np.nonzero(m)
        cx, cy = float(xs.mean()) / frame_w, float(ys.mean()) / frame_h
        zone = zone_of(cx, cy)
        inst = {
            "id": len(instances) + 1,
            "category": "unknown",
            "zone_id": zone,
            "confidence": 0.5,  # placeholder tier; label.py overwrites per source
            "label_source": "unassigned",
            "bbox_xyxy": [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())],
            "area_pct": round(area_pct, 3),
            "centroid": [round(cx, 3), round(cy, 3)],
        }
        instances.append(inst)
        zone_counts[zone] = zone_counts.get(zone, 0) + 1
        zone_coverage[zone] = zone_coverage.get(zone, 0.0) + area_pct
    # Renumber sequentially after MIN_AREA filtering (ids stay dense).
    for n, inst in enumerate(instances, start=1):
        inst["id"] = n
    return instances, zone_counts, zone_coverage
