#!/usr/bin/env python3
"""TidyQuest stage-1 runner: photo -> inventory.json + labeled overlay.png.

Pipeline: SAM masks -> dedup -> 3x3-grid zones -> per-mask category labels
(local VLM via ollama, or the transparent heuristic fallback) -> inventory
JSON conforming to schemas/inventory.schema.json (plus additive, optional
stage-1 fields: top-level and per-zone `category_counts`, per-instance
`label_source` -- see the schema note; nothing required was changed).

Usage:
  run_stage1.py <image> <outdir> [--model sam2.1_b.pt]
                [--label vlm|heuristic] [--ollama-model moondream]

Phone photos are downscaled to the 1536x2048 test-photo frame before
the SAM pass (a full-resolution phone photo OOMs the 4GB card); if the
GPU pass still OOMs, SAM retries on CPU and the inventory records
"sam_device". PYTORCH_CUDA_ALLOC_CONF=expandable_segments is set
before torch loads, to cut fragmentation on the small card.

Segmentation backends: "local" (default, SAM 2.1 on the local GPU,
no network except localhost ollama, $0) or "api" (SAM 3.1 via the Meta
Model API concept sweep -- see sam_api.py -- key from the TQ_META_API_KEY
env var only, never a CLI arg, never logged; ~$0.043/photo at list).
The API backend keeps the same frame rule, dedup, zones, labeling, and
inventory shape; only the mask source and the "model"/"sam_backend"
fields change. Default stays "local" so every existing invocation and
every verified demo number is untouched unless --sam-backend api is
passed explicitly.
"""
import argparse
import json
import os
import sys
from collections import Counter

import numpy as np
from PIL import Image, ImageDraw

# Circuit breaker (N3): consecutive per-instance VLM failures before the
# whole labeling pass falls back to heuristic. Without it, ollama dying
# mid-run would stall up to ~120s per remaining instance (label.py
# timeout) -- worst case ~34 x 120s of silent stall on the demo path.
CIRCUIT_BREAKER_LIMIT = 3

# Reduce CUDA fragmentation on the 4GB card (the OOM message itself
# suggests expandable_segments). MUST be set before torch is imported
# (segment.py imports ultralytics, below); setdefault so an explicit
# operator value wins. This covers both the CLI and the proxy, which
# spawns this runner as a subprocess.
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

# Phone photos are downscaled to fit within this frame BEFORE the SAM
# pass (see main()). The test photos are 1536x2048, so they are
# untouched by this rule and every verified demo number stands.
MAX_FRAME_LONG = 2048
MAX_FRAME_SHORT = 1536

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from segment import (  # noqa: E402
    MAX_INSTANCES, MIN_AREA_PCT, dedup_masks, instances_from_masks, run_sam)
import label as label_mod  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("outdir")
    ap.add_argument("--model", default="sam2.1_b.pt")
    ap.add_argument("--label", choices=["vlm", "heuristic"], default="vlm")
    ap.add_argument("--ollama-model", default="moondream")
    ap.add_argument("--sam-backend", choices=["local", "api"], default="local")
    args = ap.parse_args()

    os.makedirs(args.outdir, exist_ok=True)
    img = Image.open(args.image).convert("RGB")
    W, H = img.size

    # Downscale phone photos to the test-photo frame BEFORE the SAM
    # pass. A full-resolution phone photo OOMs the 4GB card (Diana's
    # real phone test, 2026-10-26: "tried to allocate 512 MiB, 415 MiB
    # free"). The rule fits the image within 1536x2048: test photos
    # (1536x2048) are untouched, and bigger photos land on the same
    # scale that already fits. NOTE on the requested "1536 on the long
    # edge": the test photos are 1536x2048 (long edge 2048), so a
    # literal 1536 long edge would shrink the test photos themselves
    # and invalidate every verified demo number -- the frame rule
    # above is what "matching the test photos" actually means.
    orig_size = (W, H)
    scale = min(MAX_FRAME_LONG / max(W, H), MAX_FRAME_SHORT / min(W, H))
    sam_image = args.image
    downscaled = False
    if scale < 1:
        new_w = max(1, round(W * scale))
        new_h = max(1, round(H * scale))
        img = img.resize((new_w, new_h), Image.LANCZOS)
        W, H = img.size
        sam_path = os.path.join(args.outdir, "_downscaled.jpg")
        img.save(sam_path, format="JPEG", quality=92)
        sam_image = sam_path
        downscaled = True
        print(f"downscaled input {orig_size} -> {(W, H)} for the SAM pass")

    if args.sam_backend == "api":
        # Live SAM 3.1 concept sweep. Key arrives ONLY via env (the
        # proxy sets TQ_META_API_KEY for its child process, request
        # scope) -- never a CLI arg (ps-visible), never logged here.
        api_key = os.environ.get("TQ_META_API_KEY")
        if not api_key:
            print("SAM API backend requires TQ_META_API_KEY env (BYOK key)")
            return 2
        import sam_api as sam_api_mod
        masks, _fw, _fh = sam_api_mod.segment_image_sync(sam_image, api_key)
        sam_device = "meta-api:sam-3.1"
        print(f"SAM device: {sam_device}")
        print(f"raw masks: {len(masks)}")
    else:
        masks, sam_device = run_sam(sam_image, args.model)
        print(f"SAM device: {sam_device}")
        print(f"raw masks: {len(masks)}")
    kept, dropped = dedup_masks(masks)
    print(f"dedup: kept {len(kept)}, dropped {len(dropped)} duplicates")
    instances, zone_counts, zone_coverage = instances_from_masks(
        masks, kept, W, H)
    print(f"instances after area filter: {len(instances)}")

    # Label each instance. VLM failure falls back to heuristic for ALL
    # instances (mixed provenance would mislead the Questmaster); the
    # fallback is reported loudly and every label is marked provisional.
    backend = args.label
    if backend == "vlm" and not instances:
        print("no instances; skipping labeling")
        backend = "none"
    if backend == "vlm":
        try:
            probe = label_mod.crop_for_instance(img, instances[0])
            import io, base64
            buf = io.BytesIO()
            probe.save(buf, format="PNG")
            label_mod._ollama_generate(
                base64.b64encode(buf.getvalue()).decode(), args.ollama_model)
            print("label backend: vlm (ollama moondream, local)")
        except Exception as e:
            print(f"VLM unavailable ({e}); falling back to heuristic pass")
            backend = "heuristic"
    if backend == "heuristic":
        print("label backend: heuristic:provisional (see label.py rules)")

    # Label each instance. The probe above falls back to heuristic for
    # ALL instances when the VLM is unavailable up front (mixed
    # provenance would mislead the Questmaster); the fallback is
    # reported loudly and every label is marked provisional. Mid-run,
    # the circuit breaker below does the same after CIRCUIT_BREAKER_LIMIT
    # consecutive VLM failures, instead of stalling per instance.
    labeled = []
    consecutive_failures = 0
    vlm_dead = False
    for inst in instances:
        if backend == "vlm" and not vlm_dead:
            try:
                cat, conf, src = label_mod.label_with_vlm(
                    img, inst, args.ollama_model)
                consecutive_failures = 0
            except Exception as e:
                consecutive_failures += 1
                print(f"VLM label failed on instance {inst['id']} ({e}); "
                      f"consecutive failures: {consecutive_failures}")
                if consecutive_failures >= CIRCUIT_BREAKER_LIMIT:
                    print(f"CIRCUIT BREAKER: {CIRCUIT_BREAKER_LIMIT} "
                          "consecutive VLM failures -- failing the whole "
                          "pass to heuristic (single provenance)")
                    vlm_dead = True
                    backend = "heuristic"
                    labeled = []  # discard partial VLM pass
                    break
                cat, conf, src = "unknown", label_mod.CONF_UNKNOWN, "vlm:failed"
        else:
            cat, conf, src = label_mod.label_heuristic(img, inst)
        labeled.append((inst, cat, conf, src))
    if vlm_dead:
        print("label backend: heuristic:provisional (circuit breaker tripped)")
        for inst in instances:
            cat, conf, src = label_mod.label_heuristic(img, inst)
            labeled.append((inst, cat, conf, src))
    for inst, cat, conf, src in labeled:
        inst["category"] = cat
        inst["confidence"] = conf
        inst["label_source"] = src

    # Category counts: overall + per zone (additive stage-1 fields).
    overall = Counter(i["category"] for i in instances)
    per_zone = {}
    for i in instances:
        per_zone.setdefault(i["zone_id"], Counter())[i["category"]] += 1
    zones = [
        {"zone_id": z, "label": z, "item_count": c,
         "coverage_pct": round(zone_coverage[z], 2),
         "category_counts": dict(sorted(per_zone[z].items()))}
        for z, c in sorted(zone_counts.items(), key=lambda kv: -kv[1])
    ]
    model_tag = (f"stage1:sam-3.1-api+{backend}"
                 if args.sam_backend == "api"
                 else f"stage1:sam2.1_b+{backend}")
    inventory = {
        "image_id": os.path.basename(args.image),
        "model": model_tag,
        "sam_backend": args.sam_backend,
        "sam_device": sam_device,
        "input_downscaled": downscaled,
        "total_items": len(instances),
        "category_counts": dict(sorted(overall.items())),
        "zones": zones,
        "instances": instances,
    }
    with open(os.path.join(args.outdir, "inventory.json"), "w") as f:
        json.dump(inventory, f, indent=2)

    # Overlay with id + category labels (boxes from instance bboxes).
    overlay = img.copy()
    draw = ImageDraw.Draw(overlay, "RGBA")
    rng = np.random.default_rng(7)
    for inst in instances:
        color = tuple(int(c) for c in rng.integers(60, 235, size=3))
        x0, y0, x1, y1 = inst["bbox_xyxy"]
        draw.rectangle([x0, y0, x1, y1], outline=color + (255,), width=3)
        draw.text((x0 + 3, y0 + 3), f"{inst['id']}:{inst['category']}",
                  fill=(255, 255, 0, 255))
    overlay.save(os.path.join(args.outdir, "overlay.png"))

    print(json.dumps({"total_items": len(instances),
                      "category_counts": dict(sorted(overall.items())),
                      "zones": zones}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
