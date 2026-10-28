#!/usr/bin/env python3
"""Privacy helpers for visual outputs: blur faces AND mirror reflections.

Stage 3 (2026-10-26) fills in the mirror boxes stage 2 deliberately left
empty. Boxes below were determined from the actual photos by visual
grounding (0-1000 normalized boxes converted to pixels for the 1536x2048
originals, plus a 20px safety margin, clamped to the frame):

- test_05_donna_bathroom_BEFORE.jpg: one large wooden-framed wall mirror
  (grounded 52,14,446,482). The photographer is partially visible in it.
- test_05_donna_bathroom_AFTER_wideangle.jpg: TWO wall mirrors. Left
  (grounded 0,70,375,571); right (grounded 595,34,1000,574). The
  photographer is fully visible in the right mirror (grounded reflection
  629,284,732,541) and that reflection box falls entirely inside the
  right-mirror blur box -- verified, not assumed.
- test_01_car_backseat_BEFORE.jpg: one side-view mirror, top left
  (grounded 0,67,145,139). It reflects road/grass only, no person -- but
  it is blurred anyway: mirrors are blurred as a class, not per-content.
- test_01_car_backseat_AFTER.jpg: no mirrors and no faces observed
  (human-viewed 2026-10-26); intentionally absent from the registry and
  from KNOWN_MIRROR_PHOTOS.

RULE (enforced by pipeline/card.py): a photo in KNOWN_MIRROR_PHOTOS with
no registry entry REFUSES composition. Registry entries are never guessed.

Faces: detect_faces() uses an OpenCV YuNet face detector when its model
is available (pipeline/haarcascades/face_detection_yunet_2023mar.onnx,
or TIDYQUEST_YUNET_MODEL), falling back to a Haar cascade on older
OpenCV. The stage-3 venv's opencv 5.0 has NO Haar API (CascadeClassifier
is gone), so Haar alone reports unavailable; YuNet (cv2.FaceDetectorYN)
is the working detector here, at a 0.5 score threshold that errs toward
blurring more, not less. The sample photos need no face detector: their
only faces are the mirror reflections, which the mirror boxes cover
(verified by human viewing of all four sample photos).
The card sidecar always records face_detector_available, so a card never
silently pretends faces were checked.
"""

import os

from PIL import Image, ImageFilter

DEFAULT_BLUR_RADIUS = 25
MARGIN_PX = 20

# Photo filename -> list of mirror boxes (xyxy pixels). Boxes include the
# mirror frame; blurring frame + glass is harmless and covers edge error.
MIRROR_REGISTRY = {
    # Donna's bathroom BEFORE (1536x2048): one wall mirror.
    "test_05_donna_bathroom_BEFORE.jpg": [(60, 9, 705, 1007)],
    # Donna's bathroom AFTER wideangle (1536x2048): two wall mirrors.
    "test_05_donna_bathroom_AFTER_wideangle.jpg": [
        (0, 123, 596, 1189),      # left mirror, above left sink
        (894, 50, 1536, 1196),    # right mirror, above right sink
    ],
    # Car backseat BEFORE (1536x2048): side-view mirror, top left.
    "test_01_car_backseat_BEFORE.jpg": [(0, 117, 243, 305)],
}

# Photos known to contain mirrors. A photo here WITHOUT a registry entry
# must refuse composition (see apply_privacy). Photos not listed here
# are treated as mirror-free unless a registry entry exists for them.
KNOWN_MIRROR_PHOTOS = frozenset(MIRROR_REGISTRY.keys())


def blur_box(image, box_xyxy, radius=DEFAULT_BLUR_RADIUS):
    """Return a copy of `image` with the given box blurred.

    box_xyxy is (x0, y0, x1, y1) in pixels. The rest of the image is
    untouched. Never mutates the input image.
    """
    x0, y0, x1, y1 = (int(v) for v in box_xyxy)
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(image.width, x1), min(image.height, y1)
    if x1 <= x0 or y1 <= y0:
        return image.copy()
    out = image.copy()
    region = out.crop((x0, y0, x1, y1)).filter(
        ImageFilter.GaussianBlur(radius)
    )
    out.paste(region, (x0, y0))
    return out


def blur_regions(image, boxes, radius=DEFAULT_BLUR_RADIUS):
    """Blur every box in `boxes` (list of xyxy tuples). Returns a copy."""
    out = image.copy()
    for box in boxes:
        out = blur_box(out, box, radius)
    return out


def _cascade_path():
    """Locate a Haar face cascade, or return None if none is available."""
    candidates = []
    env = os.environ.get("TIDYQUEST_FACE_CASCADE")
    if env:
        candidates.append(env)
    here = os.path.dirname(os.path.abspath(__file__))
    candidates.append(
        os.path.join(here, "haarcascades", "haarcascade_frontalface_default.xml")
    )
    try:
        import cv2

        candidates.append(
            os.path.join(
                cv2.data.haarcascades, "haarcascade_frontalface_default.xml"
            )
        )
    except Exception:
        pass
    for path in candidates:
        if path and os.path.exists(path):
            return path
    return None


def _yunet_model_path():
    """Locate a YuNet face-detection model, or None if absent."""
    env = os.environ.get("TIDYQUEST_YUNET_MODEL")
    if env:
        candidates = [env]
    else:
        candidates = []
    here = os.path.dirname(os.path.abspath(__file__))
    candidates.append(
        os.path.join(here, "haarcascades", "face_detection_yunet_2023mar.onnx")
    )
    for path in candidates:
        if path and os.path.exists(path):
            return path
    return None


def _detect_faces_yunet(image):
    """Detect faces with OpenCV YuNet. Returns (boxes, True) or ([], False)."""
    model = _yunet_model_path()
    if model is None:
        return [], False
    try:
        import cv2
        import numpy as np

        w, h = image.size
        detector = cv2.FaceDetectorYN_create(
            model, "", (w, h), score_threshold=0.5, nms_threshold=0.3
        )
        detector.setInputSize((w, h))
        _, faces = detector.detect(np.array(image.convert("RGB")))
        if faces is None or len(faces) == 0:
            return [], True
        boxes = []
        for f in faces:
            x, y, fw, fh = (int(v) for v in f[:4])
            x0, y0 = max(0, x), max(0, y)
            x1, y1 = min(w, x + fw), min(h, y + fh)
            if x1 > x0 and y1 > y0:
                boxes.append((x0, y0, x1, y1))
        return boxes, True
    except Exception:
        return [], False


def detect_faces(image):
    """Detect faces in a PIL image, YuNet first, Haar fallback.

    Returns (boxes, detector_available). boxes is a list of xyxy pixel
    tuples. When no detector is available, returns ([], False) --
    the caller MUST record that status, never pretend faces were checked.
    """
    path = _cascade_path()
    if path is not None:
        try:
            import cv2
            import numpy as np

            cascade = cv2.CascadeClassifier(path)
            if not cascade.empty():
                gray = cv2.cvtColor(
                    np.array(image.convert("RGB")), cv2.COLOR_RGB2GRAY
                )
                found = cascade.detectMultiScale(
                    gray, scaleFactor=1.1, minNeighbors=5, minSize=(30, 30)
                )
                boxes = [
                    (int(x), int(y), int(x + w), int(y + h))
                    for (x, y, w, h) in found
                ]
                return boxes, True
        except Exception:
            pass
    return _detect_faces_yunet(image)


def mirror_boxes_for(filename):
    """Mirror boxes for a photo filename (basename match), or []."""
    return list(MIRROR_REGISTRY.get(os.path.basename(filename or ""), []))


def apply_privacy(image, filename, extra_boxes=None):
    """Blur mirrors + detected faces in `image` for one photo.

    Raises ValueError when the photo is known to contain mirrors but has
    no registry entry -- an unmarked mirror must never reach a card.

    Returns (blurred_image, report) where report records mirror_boxes,
    face_boxes, face_detector_available, and total_boxes_blurred, so the
    card sidecar can prove what was actually blurred.
    """
    base = os.path.basename(filename or "")
    mirrors = mirror_boxes_for(base)
    if base in KNOWN_MIRROR_PHOTOS and not mirrors:
        raise ValueError(
            f"mirror boxes unmarked for {base}: refusing to compose a "
            "visual for a photo known to contain mirrors"
        )
    face_boxes, detector_available = detect_faces(image)
    boxes = mirrors + face_boxes + list(extra_boxes or [])
    blurred = blur_regions(image, boxes)
    report = {
        "photo": base,
        "mirror_boxes": mirrors,
        "face_boxes": face_boxes,
        "face_detector_available": detector_available,
        "total_boxes_blurred": len(boxes),
    }
    return blurred, report
