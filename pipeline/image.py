#!/usr/bin/env python3
"""Muse Image integration point -- LIVE since C4 (2026-10-27).

The $150 Meta Model API credit was confirmed in place by DC and the
key file verified live on 2026-10-27, so this module now performs real
edits through POST /v1/images/edits (model muse-image-1.0, multipart
image + prompt, b64_json WebP out, list $0.01/image). The endpoint
shape was verified empirically on 2026-10-27 (synthetic-image probe
200 + real after-vision render). Until then the hook still works with
a real re-scan photo (after_kind="photo").

BYOK: this module NEVER touches credentials. When live, the caller's
API key is injected per request via the api_key parameter (supplied by
the app layer from the user's own settings field) -- it is never read
from environment or disk inside this module, never logged, and never
present in any output.

Model id (VERIFIED 2026-10-26 from Baymax's live Meta Model API
catalog): muse-image-1.0. Endpoint VERIFIED empirically 2026-10-27:
POST /v1/images/edits, multipart (image file + prompt + model +
response_format=b64_json + n=1), returns data[0].b64_json (WebP by
default). build_after_vision_request() still takes the model id as a
parameter with the verified default.

CONNECTOR (RESOLVED 2026-10-26): the registered connector is
"custom.meta-model-api" (host api.meta.ai), confirmed by Baymax from
the vault side. Live calls below go to https://api.meta.ai/v1 with a
Bearer key.
"""

IMAGE_ENABLED = True  # flipped 2026-10-27: $150 credit + key verified live

# Verified 2026-10-26 from the live Meta Model API model catalog.
DEFAULT_IMAGE_MODEL = "muse-image-1.0"

AFTER_VISION_PROMPT = (
    "Edit this photo into its tidy 'after' state: the SAME room, the "
    "SAME camera angle, the SAME furniture -- only the clutter put away. "
    "Bags, boxes, loose paper and laundry go to where they live; surfaces "
    "are clear; nothing is added, nothing structural is removed or "
    "rearranged, no people are added. Photorealistic, matching the "
    "original photo's light and perspective."
)


def build_after_vision_prompt(inventory=None):
    """The edit instruction Image would receive for one after-vision.

    inventory (optional) is the stage-1 inventory dict; when supplied,
    its tidy category counts are appended so the edit knows what to put
    away. The prompt itself never contains credentials or personal data.
    """
    prompt = AFTER_VISION_PROMPT
    if inventory is not None:
        counts = {}
        for inst in inventory.get("instances", []):
            cat = inst.get("category", "unknown")
            if cat in ("structure",):
                continue
            counts[cat] = counts.get(cat, 0) + 1
        if counts:
            summary = ", ".join(f"{n} {c}" for c, n in sorted(counts.items()))
            prompt += f"\n\nClutter to put away in this room: {summary}."
    return prompt


def build_after_vision_request(before_photo_ref, inventory=None, model=None):
    """Build (do NOT send) the request dict Image would receive.

    before_photo_ref is an opaque reference to the user's own before
    photo (a path, URL, or base64 handle supplied by the caller -- this
    module never reads credentials). model defaults to
    DEFAULT_IMAGE_MODEL (muse-image-1.0, verified 2026-10-26 from the
    live Meta Model API catalog).
    """
    if model is None:
        model = DEFAULT_IMAGE_MODEL
    return {
        "model": model,
        "prompt": build_after_vision_prompt(inventory),
        "input_photo_ref": before_photo_ref,
        "mode": "edit",
    }


def final_card(before_path, after_render_or_photo, plan, differ=None,
               proof_mode="differ", after_kind="vision",
               out_path=None, sidecar_path=None, space_type="room"):
    """The final-card composition hook.

    When Image is live, after_render_or_photo is the Image render and
    after_kind="vision". Until then the hook still works with a real
    re-scan photo (after_kind="photo") -- the card itself never needs
    the network. Privacy and honest-numbers rules live in card.py and
    apply identically to vision renders and real photos. space_type is
    explicit ("room" default) -- never inferred from a filename.
    """
    try:
        from card import compose_card
    except ImportError:  # pragma: no cover - direct script execution
        from pipeline.card import compose_card
    return compose_card(
        before_path,
        after_render_or_photo,
        plan,
        differ=differ,
        proof_mode=proof_mode,
        after_kind=after_kind,
        out_path=out_path,
        sidecar_path=sidecar_path,
        space_type=space_type,
    )


API_BASE_URL = "https://api.meta.ai/v1"


def generate_after_vision(before_photo_path, inventory=None, model=None,
                          api_key=None, out_path=None):
    """The live Image edit call (LIVE since C4, 2026-10-27).

    Edits the before photo into its tidy "after" state via
    POST /v1/images/edits and returns the path of the render saved as
    PNG (the API returns WebP; PIL converts on save so card.py always
    sees PNG). Raises RuntimeError if IMAGE_ENABLED is False and
    ValueError without an api_key.

    BYOK: api_key is the user's own Meta Model API key, injected per
    request by the caller -- never read from environment or disk here,
    never logged, never in output.
    """
    import base64
    import io
    import json
    import os
    import tempfile
    import urllib.request
    import uuid

    if not IMAGE_ENABLED:
        raise RuntimeError("Image backend disabled.")
    if not api_key:
        raise ValueError("generate_after_vision requires api_key (BYOK)")
    if model is None:
        model = DEFAULT_IMAGE_MODEL
    with open(before_photo_path, "rb") as f:
        photo_bytes = f.read()
    prompt = build_after_vision_prompt(inventory)
    boundary = "----tq" + uuid.uuid4().hex
    body = b""

    def field(name, value):
        nonlocal body
        body += (f"--{boundary}\r\n"
                 f"Content-Disposition: form-data; name=\"{name}\"\r\n\r\n"
                 f"{value}\r\n").encode()

    def filefield(name, filename, data, ctype):
        nonlocal body
        body += (f"--{boundary}\r\n"
                 f"Content-Disposition: form-data; name=\"{name}\"; "
                 f"filename=\"{filename}\"\r\n"
                 f"Content-Type: {ctype}\r\n\r\n").encode() + data + b"\r\n"

    field("model", model)
    field("prompt", prompt)
    field("response_format", "b64_json")
    field("n", "1")
    filefield("image", os.path.basename(before_photo_path), photo_bytes,
              "image/jpeg")
    body += f"--{boundary}--\r\n".encode()
    http_req = urllib.request.Request(
        API_BASE_URL + "/images/edits", data=body,
        headers={"Authorization": "Bearer " + api_key,
                 "Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(http_req, timeout=300) as resp:
        payload = json.loads(resp.read().decode())
    b64 = payload["data"][0]["b64_json"]
    from PIL import Image
    render = Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGB")
    if out_path is None:
        fd, out_path = tempfile.mkstemp(prefix="tq_vision_", suffix=".png")
        os.close(fd)
    render.save(out_path, format="PNG")
    return out_path
