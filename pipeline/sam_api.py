#!/usr/bin/env python3
"""SAM 3.1 segmentation via the Meta Model API (LIVE since C4, 2026-10-27).

Concept-prompted segmentation: the hosted SAM 3.1 API takes ONE short
concept phrase per request -- it has no automatic "segment everything"
mode. This module sweeps the product's own quest category vocabulary
(label.py CATEGORIES, minus structure/unknown/small-item, which are not
visually groundable concepts) and unions the resulting masks. The
pipeline's existing dedup + labeling flow then runs unchanged: SAM
proposes regions, the local VLM/heuristic labeler assigns the final
category. Concept recall is model-dependent -- observed 2026-10-27 on
the car test photo: "trash" over-proposes (54 masks), "bag" matches
nothing despite VLM-labeled bags, "paper" finds 24 -- so the sweep is a
proposal mechanism, NOT a category classifier, and sweep counts are
NOT comparable to local SAM 2.1 automatic counts. Report them as
SAM-API-sweep counts, never as a drop-in replacement number.

Cost: one image charge per concept per photo (list $2.50/1k images =
$0.0025 per concept call; the 17-concept sweep is ~$0.043/photo).
Latency: concepts run concurrently (semaphore 8). A sequential sweep
measured ~95s on the test photo; concurrent calls cut that sharply.

BYOK: api_key is passed per call by the caller (proxy request scope,
or TQ_META_API_KEY env for the CLI). This module never reads
credentials from disk, never logs them, never puts them in output.
No key value appears in this file.
"""

import asyncio
import base64
import os

# Quest-shaped concept vocabulary (label.py CATEGORIES minus the
# non-groundable entries: structure, unknown, small item).
SAM_CONCEPTS = [
    "clothing", "bottle", "paper", "cardboard box", "bag", "shoe", "toy",
    "book", "bedding", "towel", "food container", "electronic", "cable",
    "furniture", "trash", "toiletry", "cleaning supply",
]

API_BASE_URL = "https://api.meta.ai/v1"
SAM_MODEL = "sam-3.1"


async def _masks_for_concept(client, data_url, concept, semaphore):
    """One concept request -> list of (bounds, width, height, raster)."""
    from meta_sam_parser import (
        SegmentationMaskRecord,
        decode_mask_to_raster,
        image_segmentation_format,
        parse_responses_stream,
    )

    async with semaphore:
        events = await client.responses.create(
            model=SAM_MODEL,
            input=[{"role": "user", "content": [
                {"type": "input_text", "text": concept},
                {"type": "input_image", "image_url": data_url},
            ]}],
            stream=True,
            metadata={"mask_encoding": "one_bit"},
        )
        parsed = parse_responses_stream(events, image_segmentation_format())
        async with parsed:
            result = await parsed.final_result()
    out = []
    for record in result.records:
        if not isinstance(record, SegmentationMaskRecord):
            continue
        raster = decode_mask_to_raster(record.mask)
        # decode returns row-major bytes; guard against degenerate masks.
        if len(raster) != record.mask.width * record.mask.height:
            continue
        out.append((record.bounds, record.mask.width, record.mask.height,
                    raster))
    return out


async def segment_image_async(image_path, api_key, concepts=None,
                              max_concurrency=8):
    """Segment one photo via SAM 3.1 API -> list of full-frame bool masks.

    Returns (masks, frame_w, frame_h). Masks are numpy bool arrays in
    the uploaded image's own coordinates. Raises ValueError without an
    api_key, and propagates API errors unchanged (no silent fallback:
    the caller decides local vs API).
    """
    import numpy as np
    from PIL import Image
    from openai import AsyncOpenAI

    if not api_key:
        raise ValueError("SAM 3.1 API segmentation requires api_key (BYOK)")
    if concepts is None:
        concepts = SAM_CONCEPTS
    img = Image.open(image_path).convert("RGB")
    frame_w, frame_h = img.size
    with open(image_path, "rb") as f:
        data_url = ("data:image/jpeg;base64,"
                    + base64.b64encode(f.read()).decode())
    semaphore = asyncio.Semaphore(max_concurrency)
    async with AsyncOpenAI(base_url=API_BASE_URL, api_key=api_key) as client:
        results = await asyncio.gather(*[
            _masks_for_concept(client, data_url, concept, semaphore)
            for concept in concepts
        ])
    masks = []
    resized = 0
    for concept_masks in results:
        for bounds, w, h, raster in concept_masks:
            arr = np.frombuffer(raster, dtype=np.uint8).astype(bool).reshape(h, w)
            box_w = bounds.right - bounds.left
            box_h = bounds.bottom - bounds.top
            if (h, w) != (box_h, box_w):
                # The API downscales large rasters (observed: width
                # capped at 1024, height scaled proportionally) while
                # bounds stay in full-image coordinates. Bounds are
                # authoritative for position/size, so rescale the shape
                # to the box with NEAREST (binary mask preserved).
                from PIL import Image as _Image
                arr = np.array(_Image.fromarray(arr).resize(
                    (box_w, box_h), _Image.NEAREST), dtype=bool)
                resized += 1
            # Clip defensively to the frame.
            x0, y0 = max(0, bounds.left), max(0, bounds.top)
            x1, y1 = min(frame_w, bounds.right), min(frame_h, bounds.bottom)
            if x1 <= x0 or y1 <= y0:
                continue
            crop = arr[(y0 - bounds.top):(y1 - bounds.top),
                       (x0 - bounds.left):(x1 - bounds.left)]
            full = np.zeros((frame_h, frame_w), dtype=bool)
            full[y0:y1, x0:x1] = crop
            masks.append(full)
    if resized:
        print(f"SAM API: {resized} masks rescaled to bounds "
              f"(API raster cap, bounds authoritative)")
    return masks, frame_w, frame_h


def segment_image_sync(image_path, api_key, concepts=None, max_concurrency=8):
    """Synchronous wrapper for pipeline code (run_stage1.py)."""
    return asyncio.run(segment_image_async(
        image_path, api_key, concepts=concepts,
        max_concurrency=max_concurrency))
