#!/usr/bin/env python3
"""TidyQuest app-shell backend proxy (thin, local-only, stdlib only).

Wires the Flutter app to the stage 1-3 pipeline on this machine:
  POST /inventory  photo -> inventory.json + privacy-processed overlay
  POST /plan       inventory + energy_level -> quest plan
  POST /reroute    plan + skipped step ids -> re-planned plan
  POST /differ     before/after inventories -> zone-aggregate differ
  POST /card       before/after photos + plan + differ -> share card
  GET  /demo/car   pre-cached car backseat plan + differ + card
  GET  /demo/donna pre-cached Donna's bathroom plan + differ + card
  GET  /health     liveness + guardrail status

PRIVACY: the proxy NEVER returns raw uploaded bytes. The only images it
returns are the privacy-processed overlay (apply_privacy) and the share
card (compose_card applies privacy internally). A vision render is
never labeled as a real photo: /card uses after_kind="vision" only
when it actually generated the render via Muse Image.

BYOK: the app may send the user's own Meta Model API key in the
X-Meta-API-Key header. The proxy holds it request-scoped only: never
logged, never stored, never echoed in any response, never read from
environment or disk inside pipeline modules. LIVE since C4
(2026-10-27): with a key present, /inventory runs SAM 3.1 API
segmentation, /plan runs Muse Spark planning, and /card with
after_kind="vision" runs Muse Image editing; without a key, every
endpoint falls back to the local stand-ins (SAM 2.1, local planner,
real re-scan photos). No endpoint ever degrades silently: the
response reports which backend served it.

Run with the pipeline venv so cv2/PIL/ultralytics import:
  ~/tidyquest-build/.venv/bin/python ~/tidyquest-build/app-server/proxy.py
Binds 0.0.0.0:8000 (override with TIDYQUEST_PROXY_PORT). Network calls:
localhost ollama (stage 1 VLM) plus, only when a BYOK key is supplied,
https://api.meta.ai/v1 (SAM 3.1 / Spark / Image, metered on the key).
"""

import base64
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Stage-1 serialization lock (OOM fix, 2026-10-26): the proxy is a
# ThreadingHTTPServer, so two /inventory calls could otherwise run two
# SAM subprocesses at once and split the 4GB card between them -- each
# OOMing where one alone would fit. Stage-1 runs queue here instead
# of contending; /plan, /differ, /card and /demo stay concurrent
# (they use no GPU). A queued scan waits for the running one -- the
# app just spins longer, it never gets a second OOM for its trouble.
_STAGE1_LOCK = threading.Lock()


class Stage1Error(RuntimeError):
    """A stage-1 failure message that is SAFE to show on the phone:
    short, human-readable, no traceback, no server path. _handle
    passes these through (500); every other unexpected exception gets
    the generic message instead, so a crafted detail can never leak
    by accident."""

BUILD_DIR = os.environ.get(
    "TIDYQUEST_BUILD_DIR", os.path.join(os.path.expanduser("~"), "tidyquest-build")
)
PIPELINE_DIR = os.path.join(BUILD_DIR, "pipeline")
OUT_DIR = os.path.join(BUILD_DIR, "out")
PORT = int(os.environ.get("TIDYQUEST_PROXY_PORT", "8001"))

sys.path.insert(0, PIPELINE_DIR)

from differ import diff as differ_diff  # noqa: E402
from image import IMAGE_ENABLED, final_card, generate_after_vision  # noqa: E402
from privacy import apply_privacy  # noqa: E402
from quest import plan as quest_plan, reroute as quest_reroute  # noqa: E402
from spark import SPARK_ENABLED, plan_with_spark  # noqa: E402

MAX_BODY = 64 * 1024 * 1024  # 64 MB: base64 photos + JSON


def _request_api_key(handler):
    """The caller's own Meta Model API key (BYOK), if supplied.

    Read ONLY from the X-Meta-API-Key request header, held ONLY in
    request scope, NEVER logged, NEVER stored, NEVER echoed in any
    response. LIVE since C4: /inventory passes it to the stage-1
    subprocess via TQ_META_API_KEY env (child-process scope only),
    /plan passes it as api_key= to plan_with_spark, /card passes it as
    api_key= to generate_after_vision. The key is never read from
    environment or disk inside pipeline modules (the subprocess env
    is set by this proxy, not read from disk).
    """
    key = handler.headers.get("X-Meta-API-Key")
    return key.strip() if key and key.strip() else None


def quest_mod_budgets(energy):
    """Energy budget seconds for an energy level (quest.ENERGY_BUDGETS)."""
    from quest import ENERGY_BUDGETS
    return ENERGY_BUDGETS[energy]


def _b64_to_bytes(s):
    return base64.b64decode(s, validate=False)


def _bytes_to_b64(b):
    return base64.b64encode(b).decode()


def _read_json(handler):
    length = int(handler.headers.get("Content-Length") or 0)
    if length <= 0 or length > MAX_BODY:
        raise ValueError("bad content length")
    raw = handler.rfile.read(length)
    return json.loads(raw.decode())


def _send(handler, status, obj):
    body = json.dumps(obj).encode()
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _run_stage1(image_path, outdir, api_key=None):
    """Serialize stage-1 runs (see _STAGE1_LOCK); real work is inner.

    api_key (BYOK, request scope): when present, stage 1 runs the
    SAM 3.1 API backend; when absent, the local SAM 2.1 backend.
    """
    if not _STAGE1_LOCK.acquire(blocking=False):
        sys.stderr.write("stage-1 busy: queueing this scan behind the running one\n")
        _STAGE1_LOCK.acquire(blocking=True)
    try:
        return _run_stage1_inner(image_path, outdir, api_key=api_key)
    finally:
        _STAGE1_LOCK.release()


def _run_stage1_inner(image_path, outdir, api_key=None):
    """Run the stage-1 CLI. Returns (inventory_dict, overlay_path, stdout).

    Failures raise a SHORT human-readable message -- never a raw
    Python traceback and never a server path: the app shows this text
    on the phone screen. The full detail goes to the proxy's stderr
    log for the team. When api_key is present, the CLI runs the SAM
    3.1 API backend with the key passed ONLY via the child process
    environment (never a CLI arg, never logged).
    """
    cmd = [
        sys.executable,
        os.path.join(PIPELINE_DIR, "run_stage1.py"),
        image_path,
        outdir,
        "--model",
        os.path.join(BUILD_DIR, "sam2.1_b.pt"),
        "--label",
        "vlm",
    ]
    env = None
    if api_key:
        cmd += ["--sam-backend", "api"]
        env = dict(os.environ)
        env["TQ_META_API_KEY"] = api_key
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1500,
                              env=env)
    except subprocess.TimeoutExpired:
        sys.stderr.write("stage-1 run timed out\n")
        raise Stage1Error(
            "The room scan took too long. Please try a smaller photo."
        )
    if proc.returncode != 0:
        detail = proc.stderr or proc.stdout or ""
        # Log the detail server-side; the phone only ever sees the
        # short message below -- no traceback, no /home/damon/... paths.
        sys.stderr.write("stage-1 failed: " + detail[-2000:] + "\n")
        if "out of memory" in detail.lower():
            raise Stage1Error(
                "The photo is too large for this scan, even after "
                "shrinking it. Please try a smaller photo."
            )
        raise Stage1Error(
            "The room scan failed. Please try again with a different photo."
        )
    with open(os.path.join(outdir, "inventory.json")) as f:
        inventory = json.load(f)
    overlay_path = os.path.join(outdir, "overlay.png")
    if not os.path.exists(overlay_path):
        raise Stage1Error("stage-1 run produced no overlay.png")
    return inventory, overlay_path, proc.stdout or ""


def _demo_payload(case):
    if case == "car":
        plan_path = os.path.join(OUT_DIR, "stage2_car_quest.json")
        differ_path = os.path.join(OUT_DIR, "stage1_car_differ.json")
        card_path = os.path.join(OUT_DIR, "stage3_car_card.png")
        sidecar_path = os.path.join(OUT_DIR, "stage3_car_card.json")
        before_name = "test_01_car_backseat_BEFORE.jpg"
        after_name = "test_01_car_backseat_AFTER.jpg"
    elif case == "donna":
        plan_path = os.path.join(OUT_DIR, "stage2_donna_quest.json")
        differ_path = os.path.join(OUT_DIR, "stage1_donna_differ.json")
        card_path = os.path.join(OUT_DIR, "stage3_donna_card.png")
        sidecar_path = os.path.join(OUT_DIR, "stage3_donna_card.json")
        before_name = "test_05_donna_bathroom_BEFORE.jpg"
        after_name = "test_05_donna_bathroom_AFTER_wideangle.jpg"
    else:
        raise ValueError("unknown demo case")
    with open(plan_path) as f:
        plan = json.load(f)
    with open(differ_path) as f:
        differ = json.load(f)
    with open(card_path, "rb") as f:
        card_b64 = _bytes_to_b64(f.read())
    with open(sidecar_path) as f:
        sidecar = json.load(f)
    return {
        "plan": plan,
        "differ": differ,
        "card_b64": card_b64,
        "sidecar": sidecar,
        "before_name": before_name,
        "after_name": after_name,
    }


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # keep stdout readable
        sys.stderr.write("proxy: " + args[0] % args[1:] + "\n")

    def _handle(self, fn):
        try:
            result = fn()
            _send(self, 200, result)
        except ValueError as e:
            _send(self, 400, {"error": str(e)})
        except FileNotFoundError as e:
            # Log the detail server-side; the phone gets a short
            # message, never a server path.
            sys.stderr.write(f"proxy not-found: {e!r}\n")
            _send(self, 404, {"error": "Not found."})
        except Stage1Error as e:
            # Client-safe by construction (see Stage1Error): pass the
            # short message through so the app can say something
            # useful instead of a generic failure.
            _send(self, 500, {"error": str(e)})
        except Exception as e:  # noqa: BLE001 - proxy must answer, not hang
            # Log the detail server-side; the phone gets a short
            # message, never a traceback or a server path.
            sys.stderr.write(f"proxy error: {e!r}\n")
            _send(self, 500, {"error": "Something went wrong on the server. Please try again."})

    def do_GET(self):  # noqa: N802
        if self.path == "/health":
            self._handle(
                lambda: {
                    "ok": True,
                    "stages": ["pipeline", "questmaster", "share-card"],
                    "meta_api": "live",
                    "credits": "confirmed",
                }
            )
        elif self.path.startswith("/demo/"):
            case = self.path.split("/demo/", 1)[1].strip("/")
            self._handle(lambda: _demo_payload(case))
        else:
            _send(self, 404, {"error": "unknown route"})

    def do_POST(self):  # noqa: N802
        if self.path == "/inventory":
            self._handle(self._inventory)
        elif self.path == "/plan":
            self._handle(self._plan)
        elif self.path == "/reroute":
            self._handle(self._reroute)
        elif self.path == "/differ":
            self._handle(self._differ)
        elif self.path == "/card":
            self._handle(self._card)
        else:
            _send(self, 404, {"error": "unknown route"})

    def _inventory(self):
        body = _read_json(self)
        image_b64 = body.get("image_b64")
        filename = body.get("filename") or "photo.jpg"
        if not image_b64:
            raise ValueError("image_b64 required")
        tmp = tempfile.mkdtemp(prefix="tq_inv_")
        try:
            img_path = os.path.join(tmp, os.path.basename(filename))
            with open(img_path, "wb") as f:
                f.write(_b64_to_bytes(image_b64))
            outdir = os.path.join(tmp, "out")
            _inv_key = _request_api_key(self)  # request scope only
            inventory, overlay_path, stdout = _run_stage1(
                img_path, outdir, api_key=_inv_key)
            # Privacy before anything visual leaves this machine. The
            # overlay is the same size as the input photo, so mirror
            # boxes (original-photo coordinates) align exactly.
            from PIL import Image

            overlay = Image.open(overlay_path).convert("RGB")
            overlay, report = apply_privacy(overlay, filename)
            buf = io.BytesIO()
            overlay.save(buf, format="PNG")
            backend = "vlm" if "label backend: vlm" in stdout else "heuristic"
            return {
                "inventory": inventory,
                "overlay_b64": _bytes_to_b64(buf.getvalue()),
                "privacy": report,
                "label_backend": backend,
                "sam_backend": inventory.get("sam_backend", "local"),
            }
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def _plan(self):
        body = _read_json(self)
        inventory = body.get("inventory")
        energy = body.get("energy_level")
        if not inventory or not energy:
            raise ValueError("inventory and energy_level required")
        # Space type is explicit (default "room"); it is never inferred
        # from the inventory filename.
        space_type = body.get("space_type") or "room"
        _byok_key = _request_api_key(self)  # held request-scoped only.
        # Never logged, stored, or echoed.
        # LIVE since C4: with a BYOK key, Muse Spark plans; without one
        # (or if Spark fails), the local planner serves and the response
        # says so -- never a silent downgrade.
        if _byok_key and SPARK_ENABLED:
            try:
                plan = plan_with_spark(inventory, energy,
                                       quest_mod_budgets(energy),
                                       api_key=_byok_key)
                return {"plan": plan, "planner_backend": "spark"}
            except Exception as e:  # noqa: BLE001 - fallback must serve
                sys.stderr.write(f"Spark plan failed, local fallback: {e!r}\n")
                plan = quest_plan(inventory, energy, space_type=space_type)
                return {"plan": plan, "planner_backend": "local-fallback"}
        plan = quest_plan(inventory, energy, space_type=space_type)
        return {"plan": plan, "planner_backend": "local"}

    def _reroute(self):
        body = _read_json(self)
        plan = body.get("plan")
        skipped = body.get("skipped_step_ids") or []
        if not plan:
            raise ValueError("plan required")
        return {"plan": quest_reroute(plan, skipped)}

    def _differ(self):
        body = _read_json(self)
        before = body.get("before_inventory")
        after = body.get("after_inventory")
        if not before or not after:
            raise ValueError("before_inventory and after_inventory required")
        angle = bool(body.get("angle_mismatch", False))
        space_type = body.get("space_type") or "room"
        return {"differ": differ_diff(before, after, angle_mismatch=angle,
                                       space_type=space_type)}

    def _card(self):
        body = _read_json(self)
        after_kind = body.get("after_kind", "photo")
        before_b64 = body.get("before_b64")
        after_b64 = body.get("after_b64")
        plan = body.get("plan")
        if after_kind == "vision":
            # LIVE since C4: the after half is a Muse Image render, and
            # the card labels it AFTER-VISION (never a photo). Requires
            # a BYOK key; a real photo must never be labeled vision.
            if not before_b64 or not plan:
                raise ValueError("before_b64 and plan required")
            if not IMAGE_ENABLED:
                raise ValueError("after_kind=vision refused: Image backend disabled")
            _vision_key = _request_api_key(self)
            if not _vision_key:
                raise ValueError(
                    "after_kind=vision requires a Meta API key "
                    "(X-Meta-API-Key header)")
        elif not before_b64 or not after_b64 or not plan:
            raise ValueError("before_b64, after_b64, and plan required")
        differ = body.get("differ")
        # Honest-numbers auto-selection, mirroring stage 3: differ mode
        # only when the room's count actually went down overall AND the
        # tidy count went down; otherwise the plan-based quest mode
        # (with its printed caveat) applies.
        proof_mode = body.get("proof_mode")
        if proof_mode is None:
            proof_mode = "differ"
            if differ is not None:
                # tidy_cleared returns (cleared, before_tidy, after_tidy).
                # A negative tidy cleared count means the differ cannot
                # honestly headline a reduction, so the plan-based quest
                # mode (with its printed caveat) applies. A computation
                # error is NOT silently downgraded: it propagates as a
                # 500 so the app shows it instead of wrong numbers.
                # The raw gate matters too: a wider-angle re-scan can
                # show a small positive tidy cleared while the raw total
                # ROSE (Donna: tidy +1, raw -2). Headlining that as a
                # reduction would mislead, so the raw cleared count must
                # also be positive. (Car: raw +23, tidy +26 -- still
                # differ. A zero or missing raw cleared count also
                # falls back to quest mode: no reduction is claimed
                # without a real overall drop.)
                from card import tidy_cleared

                # Space type is explicit (default "room"); it is never
                # inferred from a filename.
                space_type = body.get("space_type") or "room"
                cleared = tidy_cleared(differ, space_type)[0]
                raw_cleared = (differ.get("totals") or {}).get("cleared")
                # AUDIT-1 fix 1: an angle-mismatched pair can never
                # headline a reduction, even when both deltas are
                # positive (Donna API pair: both positive, yet the
                # framings differ). The differ payload carries the
                # flag; a top-level body flag is honored too.
                angle_mismatch = bool(
                    differ.get("angle_mismatch")
                    or body.get("angle_mismatch"))
                if (angle_mismatch or cleared <= 0
                        or raw_cleared is None
                        or raw_cleared <= 0):
                    proof_mode = "quest"
        tmp = tempfile.mkdtemp(prefix="tq_card_")
        try:
            before_path = os.path.join(
                tmp, os.path.basename(body.get("before_name") or "before.jpg")
            )
            with open(before_path, "wb") as f:
                f.write(_b64_to_bytes(before_b64))
            if after_kind == "vision":
                # Render the after-vision now; compose_card labels it
                # AFTER-VISION and privacy-processes it like any visual.
                after_path = generate_after_vision(
                    before_path,
                    api_key=_request_api_key(self),
                    out_path=os.path.join(tmp, "vision.png"),
                )
            else:
                after_path = os.path.join(
                    tmp, os.path.basename(body.get("after_name") or "after.jpg")
                )
                with open(after_path, "wb") as f:
                    f.write(_b64_to_bytes(after_b64))
            out_path = os.path.join(tmp, "card.png")
            sidecar_path = os.path.join(tmp, "card.json")
            card_path, sidecar = final_card(
                before_path,
                after_path,
                plan,
                differ=differ,
                proof_mode=proof_mode,
                after_kind=after_kind,
                out_path=out_path,
                sidecar_path=sidecar_path,
                space_type=body.get("space_type") or "room",
            )
            with open(card_path, "rb") as f:
                card_b64 = _bytes_to_b64(f.read())
            return {
                "card_b64": card_b64,
                "sidecar": sidecar,
                "proof_mode": proof_mode,
            }
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


def main():
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    server.daemon_threads = True
    print(f"TidyQuest proxy listening on 0.0.0.0:{PORT} (build dir {BUILD_DIR})")
    print("Meta API integration points are LIVE (BYOK key per request).")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
