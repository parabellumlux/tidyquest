# TidyQuest — Build Notes
Started 2026-10-22 on Damon's go ("start the build and code"). Owner split (updated 2026-10-26): Damon = applicant; Baymax = fleet/build partner; Remy = BUILDER from stage 1 on (assigned by Damon + Baymax, 2026-10-26); Diana = approve/film/test. Remy still owns spec, strategy, application, demo, submission.

## Pipeline (the product in one line)
Photo → **SAM 3.1** inventory (schemas/inventory.schema.json) → **Muse Spark** Questmaster plan (schemas/quest.schema.json) → quest engine (check-offs, no-shame re-route) → progress re-scan + diff → **Muse Image** after-vision (shown at start) + before/after share card (at completion).

## What exists now
- `schemas/` — the two contracts everything is built against. Change the schema, change the product; argue here first.
- `spike/segment_spike.py` — v0 segmentation spike. Ultralytics SAM 2.1 locally (stand-in for SAM 3.1 via Meta Model API — the schema is the contract, the backend is one config swap). Emits inventory.json + overlay.png. v0 = geometry (counts/zones/coverage); labels come from the vision pass.
- `../test-set/` — Diana's real before/after photos. test_01 (car backseat) is the stress test and differ case #1.

## Run the spike (on luci)
```
cd ~/tidyquest-spike
python3 -m venv --system-site-packages .venv   # reuse system torch 2.13 + CUDA
.venv/bin/pip install ultralytics pillow
.venv/bin/python segment_spike.py test_01_car_backseat_BEFORE.jpg out/test_01
```

## Decisions already made
- Bounded scope: ONE space per quest (room or car interior). No whole-house mode in v1.
- Energy sizing is a hard constraint, not a flavor: plan must fit the declared budget (quest.schema energy_level).
- Progress is proven by re-scan diff, never self-report. Before/after pairs in the test set are the differ's grading set.
- Credit discipline: ~1 SAM + ~3 Spark + 1–2 Image calls per session; cache aggressively. $150 API budget is engineered, not hoped for.
- Privacy: faces blurred on share cards; family test photos are internal only.

## Open questions for the fleet (Baymax/Damon — asked 2026-10-22)
1. Repo home: Forgejo/sourceoftruth, or a fresh tidyquest repo on luci?
2. Spike hosting: keep inference on luci's GTX 1080 for dev; what's the demo-day serving plan (Baseten/Modal credits are in the prize kit)?
3. App shell: fresh Flutter project, or reuse Mia's shell/auth scaffolding to save days?
4. Questmaster tone: one warm voice, or selectable (gentle / playful / drill-sergeant-but-kind)?
5. Vision labeling pass for the spike: local VLM on luci, or Groq-hosted Llama vision with the household key, until Muse Spark access lands?

## Stage 1 (2026-10-26, Remy builds)
Pipeline code lives in `pipeline/` (segment.py, label.py, differ.py, run_stage1.py).
- SAM 2.1 local stand-in (sam2.1_b.pt) -> masks -> dedup -> 3x3-grid zones -> per-mask labels -> inventory.json + labeled overlay.png.
- Dedup rule: largest-first; drop only near-duplicates (IoU >= 0.5, or >=95% contained AND >=half the kept mask's area). A small real object on a big one survives. Car BEFORE: 38 raw masks -> 4 dropped -> 34 instances (matches Baymax's 4 duplicate pairs).
- Labels: LOCAL VLM only (ollama moondream, 1.7B, pulled 2026-10-26, $0, no external calls). Confidence values are documented placeholder tiers per label source (vlm 0.7, heuristic 0.3), NOT measurements, until SAM 3.1 API returns real scores. Heuristic fallback (label.py) is provisional and says so.
- Differ: zone-aggregate ONLY (per-zone counts/coverage/category deltas). Never per-instance matching. Handles different-angle pairs (Donna's bathroom) with an explicit caveat in the output.
- Schema note (FLAGGED, additive only): inventory.schema.json gained optional `category_counts` (top-level + per-zone) and per-instance `label_source`. Nothing previously required changed; old consumers still validate.
- GPU note: torch 2.13 has no kernels for the GTX 1080 (sm_61); runs land on the 4GB card (device 0) with one OOM warning on the car photo but complete output. Watch item for larger room photos; CPU fallback if a photo OOMs.
- Privacy: faces blurred on share cards. Family test photos internal only, EXCEPT Donna's bathroom, which Diana explicitly cleared for the submitted demo video (2026-10-26).
- HARD GUARDRAIL: NO Meta Model API calls until Diana confirms the $150 free credits show in Billing. API integration points are coded later; stage 1 uses local models only. $0 spent.

## Stage 2 (2026-10-26, Remy builds)
Questmaster planner + engine in `pipeline/` (quest.py, spark.py,
privacy.py, validate_quest.py, run_stage2.py, test_stage2.py).
- inventory.json -> quest.schema.json plan: zones become quests (smallest
  first), largest tidy-able zone = Boss Battle, trash-first ordering,
  Mystery Box steps for unknown/low-confidence items, surface reset last.
- Energy budgets are HARD (barely-standing 300s / 10-minutes 600s /
  an-hour 3600s / full-send unlimited); no-shame reroute included.
- FILTER (approved): `structure` always out of quest counts; vehicle
  `furniture` (car seats) out too.
- Spark integration point coded but NOT called (no live calls until free
  credits confirmed in Billing; no key handling anywhere).
- Privacy: faces AND mirror reflections blurred before any demo/public use;
  mirror boxes marked by a human first (registry starts empty by design).
- Samples: out/stage2_car_quest.json, out/stage2_donna_quest.json.
  ALL STAGE 2 CHECKS PASSED. $0 spent.

## App shell (added 2026-10-26)

Flutter app in `app/` (package com.ignisalgos.tidyquest). Run the backend
proxy first, then the app:

```
# On luci, from ~/tidyquest-build:
~/tidyquest-build/.venv/bin/python app-server/proxy.py   # port 8001
# (override with TIDYQUEST_PROXY_PORT; port 8000 is taken by immich_ml)

# Build the debug APK:
cd app && flutter build apk --debug
# -> build/app/outputs/flutter-apk/app-debug.apk
```

In the app, the proxy URL field defaults to
http://100.102.190.123:8001 (luci's Tailscale IP; editable in-app). The
phone must be on the same Tailscale network to reach the proxy. Demo
mode (car backseat / Donna's bathroom) works without a photo, using
pre-cached plan + differ + card outputs.

Privacy: every displayed or shared visual passes through
privacy.apply_privacy() — faces (YuNet) and mirror reflections (registry)
blurred. The proxy never returns raw uploaded bytes, only
privacy-processed overlays and cards. A new user photo has no mirror
registry entry, so only faces are blurred for it; the four sample photos
have mirror boxes. after_kind="vision" is refused by the proxy until
free credits are confirmed.

NO Meta API calls in this shell. $0 spent.
