# TidyQuest Stage 1 — Progress Log (Remy builds, 2026-10-26)

## What stage 1 is
SAM 2.1 local stand-in -> mask dedup -> 3x3-grid zones -> per-mask labels from
a LOCAL VLM (ollama moondream 1.7B, pulled 2026-10-26, $0, no external calls)
-> inventory.json + labeled overlay.png -> zone-aggregate differ.
No Meta Model API calls were made. $0 spent.

## Files
- `pipeline/segment.py` — SAM + dedup + zones.
- `pipeline/label.py` — VLM labeling (describe + keyword map) + provisional heuristic fallback.
- `pipeline/differ.py` — zone-aggregate differ (never per-instance matching).
- `pipeline/run_stage1.py` — end-to-end runner.
- `pipeline/validate.py` — schema/consistency checks for inventory.json.
- `schemas/inventory.schema.json` — ADDITIVE ONLY change (flagged): optional
  `category_counts` (top-level + per-zone) and per-instance `label_source`.
  Nothing previously required changed. `quest.schema.json` untouched.
- `out/stage1_car_before_vlm/`, `out/stage1_car_after_vlm/`,
  `out/stage1_donna_before_vlm/`, `out/stage1_donna_after_vlm/` — inventories + overlays.
- `out/stage1_car_differ.json`, `out/stage1_donna_differ.json` — differ outputs.
- `out/stage1_car_before/` — heuristic-fallback baseline (pre-VLM fix), kept for comparison.

## Results (VLM labels, moondream local)

### Car backseat pair (DIFFERENT ANGLE — see note)
- BEFORE: 38 raw masks -> 4 deduped -> 34 items.
  Categories: bag 13, cardboard box 7, paper 3, furniture 2, bottle 1,
  towel 1, structure 1, unknown 6. Coverage 53.2%.
- AFTER: 14 raw -> 3 deduped -> 11 items.
  Categories: cleaning supply 1 (the spray bottle, correctly labeled),
  furniture 4 (seats), structure 2, unknown 4. Coverage 20.1%.
- DIFFER: 34 -> 11, cleared 23 (67.6%).
  Category deltas: bag -13, cardboard box -7, paper -3, bottle -1,
  towel -1, unknown -2; cleaning supply +1 (spray bottle only in AFTER
  framing), furniture +2, structure +1 (framing artifacts).
  Pile zones (lower center, middle right, upper center) went to -100%.
  Residual 11 are seats/door/spray bottle, not clutter.
  This pair IS the clean progress proof for the demo.

### Donna's bathroom pair (different angle, wide AFTER)
- BEFORE: 37 raw -> 5 deduped -> 27 items (5 more dropped by area filter).
  Categories: bedding 2, book 1, bottle 1, cable 1, cardboard box 2,
  clothing 3, electronic 1, paper 2, structure 4, unknown 10. Coverage 45.7%.
- AFTER (wideangle): 39 raw -> 8 deduped -> 29 items.
  Categories: bottle 2, cardboard box 1, cleaning supply 1, electronic 1,
  paper 1, structure 7, toiletry 1, unknown 15. Coverage 70.2%.
- DIFFER: 27 -> 29, cleared -2 (-7.4%), angle-mismatch flagged.
  Clutter categories DID fall (bedding -2, clothing -3, cardboard box -1,
  paper -1, book -1, cable -1 = -9), but the wider AFTER framing added
  structural masks (structure +3) and unknowns (+5), swamping the reduction.
  HONEST READ: the differ runs correctly on different-angle pairs, but this
  pair does NOT yield a clean demo reduction number. Use the car pair for the
  differ proof; use Donna's bathroom (cleared for the demo by Diana
  2026-10-26) for hero inventory/quest visuals. A matched-angle Donna AFTER
  would need a re-photo from the BEFORE angle — never a re-clean (standing
  promise) — and only if the room is still tidy.

## Bugs found and fixed
- Moondream degenerates on constrained one-word prompts (observed outputs:
  'urn', ''). Fix: ask it to describe freely (accurate), then map the
  description to the quest vocabulary with an ordered keyword map in label.py.
- luci login shell is fish: no bash for-loops or heredocs in ssh commands;
  use simple commands, scp'd scripts, or `bash -s`.

## Watch items / blockers
- GPU: torch 2.13 has no kernels for the GTX 1080 (sm_61). Runs land on the
  4GB card (device 0) with one OOM warning on the car photo but complete
  output. Larger room photos may OOM; CPU fallback is the escape hatch.
- SAM undercounts dense piles (true car pile is 60+ items; we count 34) and
  segments structure (roof, seats, mirrors, cabinets) alongside clutter.
  Stage 2 (Questmaster) should treat `structure` (and car `furniture` seats)
  as non-quest items, and the demo's "items cleared" line should count
  tidy-able categories, not raw totals.
- Unknown rate: car BEFORE 6/34 (18%), Donna BEFORE 10/27 (37%). Unknowns
  become Mystery Box steps downstream — acceptable, and honest.
- PRIVACY: the photographer is visible in the Donna bathroom mirrors (partial
  reflection in BEFORE, full reflection with flash in AFTER). Blur mirror
  reflections as well as faces before any demo/public use.

## What stage 2 (Questmaster) needs
- Inventory contract is stable (additive fields only). Questmaster consumes
  inventory.json: zones -> quests, largest zone -> Boss Battle, low-confidence
  / unknown items -> Mystery Box steps, trash-first ordering.
- Decision needed: filter `structure` (and vehicle `furniture`) out of quest
  item counts, or count them? Recommend filter (stage-1 numbers above show why).
- No Meta API calls until Diana confirms $150 free credits in Billing.
  When live: swap SAM 2.1 local -> SAM 3.1 API, moondream -> Muse Spark
  vision labels (real confidences), and build the Image after-vision + card.

## Stage 2 (2026-10-26, Remy builds)
Questmaster planner + quest engine (pipeline/quest.py) consuming inventory.json,
emitting quest.schema.json plans.
- Zones -> quests, smallest first; largest tidy-able zone = Boss Battle
  (car: middle right, 6 tidy items; Donna: lower right, 7). Budget HARD:
  barely-standing 300s, 10-minutes 600s, an-hour 3600s, full-send unlimited;
  steps atomic, greedy fit in plan order, est_minutes = ceil(total/60).
- FILTER (approved 2026-10-26): structure always out; furniture out in
  vehicle interiors. Car: 34 -> 31 tidy (1 structure + 2 furniture out).
  Donna: 27 -> 23 tidy (4 structure out). Filtered items never in
  item_refs or counts.
- Ordering: trash-first (paper, cardboard box), then categories A-Z,
  Mystery Box last per zone. Mystery = category unknown OR confidence < 0.7.
  Steps <= 4 items, 25+25n sec capped at 115. Last step of every plan =
  surface reset (60s, when it fits the budget).
- No-shame re-route: reroute() drops skipped steps, recomputes est_minutes,
  never scolds; skip-all -> single rest step. Boss flag marks the room's
  largest zone when included; budgeted plans that never reach it
  legitimately carry no boss (validator allows 0-or-1, and requires a
  present boss to be the true largest zone).
- Spark integration point (pipeline/spark.py): builds the planner prompt +
  request shape, parses responses, but plan_with_spark() raises until free
  credits confirmed. NO live calls, NO key handling anywhere. Model id
  deliberately NOT guessed -- must come from Meta Model API docs when live.
- Privacy (pipeline/privacy.py): blur helper for faces AND mirror
  reflections; MIRROR_REGISTRY starts empty BY DESIGN -- mirror boxes must
  be marked by a human from the actual photos before any demo/public use.
  Stage 2 outputs are JSON only, so no visuals were produced or published.
- Validation (pipeline/validate_quest.py + test_stage2.py): every inventory
  x every energy level validates against quest.schema.json; budget hard;
  full-send covers every tidy item exactly once; filter holds; reroute +
  rest-plan cases pass; Spark refusal cases pass. ALL STAGE 2 CHECKS PASSED.
- Samples (an-hour): out/stage2_car_quest.json -- "Car Backseat Reclaim",
  9 quests, 20 steps, 22 min, 31/31 tidy items, boss middle right.
  out/stage2_donna_quest.json -- "Donna's Bathroom Reclaim", 8 quests,
  18 steps, 18 min, 23/23 tidy items, boss lower right.
- Schemas UNTOUCHED in stage 2 (quest.schema.json unchanged; inventory
  additive fields from stage 1 unchanged).
- $0 spent. NO Meta API calls.

## Stage 3 (2026-10-26, Remy builds) -- after-vision + share card, NON-CALL parts
Share-card composer + Muse Image integration point, coded but never
called. No Meta Model API calls. $0 spent.

- `pipeline/card.py` -- compose_card(): before/after split, quest stats
  in the plan's card_text format, quest title, #TidyQuest. Privacy runs
  BEFORE compositing via privacy.apply_privacy(); an unmarked known-
  mirror photo refuses outright (no card written).
- `pipeline/privacy.py` -- MIRROR_REGISTRY FILLED (stage 2 left it empty
  by design). Boxes determined from the actual photos by visual
  grounding (0-1000 boxes -> pixels for the 1536x2048 originals, +20px
  safety margin, clamped):
  - test_05_donna_bathroom_BEFORE.jpg: [(60, 9, 705, 1007)] -- one wall
    mirror; photographer partially visible in it.
  - test_05_donna_bathroom_AFTER_wideangle.jpg: [(0, 123, 596, 1189),
    (894, 50, 1536, 1196)] -- two wall mirrors; photographer fully
    visible in the right one (grounded reflection box falls entirely
    inside the right-mirror blur box -- verified, not assumed).
  - test_01_car_backseat_BEFORE.jpg: [(0, 117, 243, 305)] -- side-view
    mirror (road/grass only, no person) blurred as a class anyway.
  - test_01_car_backseat_AFTER.jpg: no mirrors, no faces observed
    (human-viewed); intentionally absent from the registry.
  Faces: detect_faces() via OpenCV Haar when a cascade is available.
  The stage-3 venv's opencv 5.0 ships NO cascade file, so the detector
  reports unavailable and the card sidecar records that status -- a card
  never pretends faces were checked. Sample photos need no detector:
  their only faces are the mirror reflections, covered by mirror blur
  (verified by human viewing of all four sample photos). Drop a Haar
  cascade into pipeline/haarcascades/ (or set TIDYQUEST_FACE_CASCADE)
  to activate face detection; that is an app-shell-stage item.
- `pipeline/image.py` -- Muse Image integration point, mirroring
  spark.py: IMAGE_ENABLED=False; build_after_vision_request() refuses
  without a model id (must come from the Meta Model API model catalog
  when live -- NOT guessed); final_card() delegates to card.py and
  works today with real re-scan photos (after_kind="photo"), ready for
  vision renders (after_kind="vision") when live; generate_after_vision()
  raises until credits are confirmed.
- HONEST NUMBERS, enforced in code (not just documented):
  - Car card (proof_mode="differ"): "26 items · 22 minutes · 1 space
    reclaimed". 26 = TIDY-only cleared from the differ's category
    deltas (structure + vehicle furniture excluded); raw 34 -> 11 rides
    in the sidecar, never as the headline.
  - Donna card (proof_mode="quest"): "23 items · 18 minutes · 1 space
    reclaimed" (plan items + plan minutes) PLUS the caveat printed on
    the card: "Different camera angles -- counts reflect the quest
    plan, not a matched before/after count." The differ's 27 -> 29
    (-2, angle mismatch) is recorded in the sidecar as NOT SHOWN, and
    the card carries reduction_claim=False. Differ mode refuses a
    negative tidy cleared count outright.
- Samples (verified visually): out/stage3_car_card.png + .json sidecar;
  out/stage3_donna_card.png + .json sidecar. Both mirrors blurred on
  the Donna card (photographer unrecognizable); car side mirror blurred
  on the car card. Label rendering fixed (anchor "lt" so BEFORE/AFTER
  labels sit exactly inside their boxes).
- Validation: pipeline/test_stage3.py -- 5 checks, ALL PASSED (blur
  pixel behavior + input immutability, mirror refusal, tidy cleared =
  26, stats modes + negative refusal, Image refusals).
- Runner: pipeline/run_stage3.py composes both sample cards.
- Schemas UNTOUCHED in stage 3.

## What the app shell stage needs
- A Haar cascade file for face detection (pipeline/haarcascades/) OR an
  explicit decision that mirror-registry + human review covers faces.
- When free credits land: Muse Image model id + endpoint + envelope
  from the Meta Model API docs (then flip IMAGE_ENABLED, same pattern
  as SPARK_ENABLED); the after-vision render feeds final_card() with
  after_kind="vision".
- App shell consumes: card.compose_card() + image.final_card() +
  quest.plan/reroute() + differ.diff(); sidecar JSONs are the audit
  trail (stats, proof mode, privacy report) for every published card.

## App shell — 2026-10-26 (Flutter shell wiring stages 1–3, local only)

Flutter app created at `app/` (package com.ignisalgos.tidyquest, org
com.ignisalgos). Screens in order: home (photo capture/pick + proxy URL
field + demo-mode entry) -> inventory overlay -> energy picker
(barely-standing / 10-minutes / an-hour / full-send) -> quest list with
check-offs, Skip/Unskip, and no-shame re-route -> re-scan (after photo,
optional different-angle flag) -> differ proof -> share card display
(share via system sheet in live flow; demo flow shows the card without
a share button).

Backend proxy at `app-server/proxy.py` (stdlib-only ThreadingHTTPServer,
default port 8001 — port 8000 on luci belongs to an unrelated immich_ml
service, so it was NOT taken). Endpoints: GET /health, POST /inventory
(SAM 2.1 + VLM labels, privacy-processed overlay only), POST /plan,
POST /reroute, POST /differ, POST /card (after_kind="vision" REFUSED;
honest-numbers auto-selection: differ mode only when tidy cleared >= 0,
else quest mode), GET /demo/car and /demo/donna (pre-cached plan +
differ + card + sidecar). The proxy NEVER returns raw uploaded bytes.

Verified 2026-10-26: `flutter analyze` clean; widget smoke test passes;
proxy end-to-end tests pass (plan 9 quests/20 steps/22 min for the car
photo; reroute drops a skipped step and recomputes minutes; differ
34 -> 11, cleared 23/67.6%; card in differ mode reads "26 items .
22 minutes . 1 space reclaimed", matching stage 3 exactly; /inventory
on the car BEFORE photo returns 34 items via VLM with mirror + 1 YuNet
face region blurred). APK built: `app/build/app/outputs/flutter-apk/
app-debug.apk` (debug build; APK routing per Damon's rule: to Diana
first in main chat, she forwards to Baymax).

App-shell bug found and fixed (proxy, not pipeline): /card's honest-
numbers auto-selection compared tidy_cleared()'s tuple to an int, so
every card silently fell back to quest mode. Fixed to unpack
cleared = tidy_cleared(...)[0] and to propagate computation errors
instead of silently downgrading.

AndroidManifest: INTERNET + CAMERA permissions, usesCleartextTraffic
(true — the proxy is plain HTTP on the household Tailscale network).

NO Meta API calls. $0 spent. Stages 1-3 schemas untouched.

## Hero demo — 2026-10-26 (Donna's bathroom end-to-end + pre-cached outputs)

Verified end-to-end through the app-shell proxy (luci port 8001, live
flow AND pre-cached demo endpoints), then frozen into `demo/` so the
submission video never depends on a live run. $0 spent. NO Meta API
calls. Schemas untouched.

- Live /inventory (Donna BEFORE, SAM 2.1 + local VLM): reproduced the
  cached stage-1 inventory EXACTLY — 27 items, identical categories
  (bedding 2, book 1, bottle 1, cable 1, cardboard box 2, clothing 3,
  electronic 1, paper 2, structure 4, unknown 10); mirror box blurred.
- Live /plan (an-hour): "Donna's Bathroom Reclaim", 8 quests, 18 steps,
  18 min, boss = lower right; sample step: "Mystery Box in upper
  center: open it, sort what you find -- keep, toss, or rehome."
- Live /reroute (skip middle-right-s1): step dropped, 17 steps,
  17 min, intro unchanged — no scolding. NOTE: reroute() sets NO
  explicit `rerouted` flag (stage-2 log said one would exist);
  behavior verified, flag is a validator nicety, not a demo blocker.
- Live /differ (Donna): 27 -> 29, cleared -2 (-7.4%),
  angle_mismatch: true. NEVER a reduction — quest mode + caveat only.
- Live /differ + /plan + /card (car): 9 quests, 20 steps, 22 min, boss
  middle right; differ 34 -> 11, cleared 23 (67.6%); card differ mode,
  "26 items · 22 minutes · 1 space reclaimed" (tidy-only).
- HERO-DEMO BUG FOUND AND FIXED (app-server/proxy.py, /card
  auto-selection): the old rule headlined the Donna pair as "1 items"
  in differ mode, because tidy cleared is +1 (structure excluded) even
  though raw cleared is -2. The rule now requires BOTH raw cleared > 0
  AND tidy cleared > 0 for differ mode; zero/missing raw also falls
  back to quest mode. After fix: Donna auto-selects quest mode
  ("23 items · 18 minutes", reduction_claim false, caveat printed);
  car still auto-selects differ mode. Existing proxy test passes
  (plan 22 min/9 quests/20 steps; reroute 19 steps/21 min; differ
  34 -> 11; card differ/26 items; mirror blurred). Proxy restarted on
  luci port 8001 and re-verified healthy.
- `demo/` (24 files): canonical stage 1-3 outputs for both rooms,
  live rerouted plan, demo_run_report.json, privacy-processed visuals
  only (no raw photos). Donna overlay BEFORE human-verified: mirror
  blur wash, labeled item boxes, photographer unrecognizable.
  Privacy nuance: face detection on overlays flagged 1-2 extra small
  face-like regions (almost certainly detector noise on segmentation
  colors); all are blurred — blurring extra is safe.
- `demo/DEMO_SCRIPT.md`: 7-shot, ~60-90s arc for Diana to film/approve
  (messy photo -> zones -> quest list -> skip + kind re-route ->
  re-scan + card -> car proof -> close), with the honest-numbers rule
  and the NEVER list stated on it.
- Gaps for polish/video stage: live Meta API renders (SAM 3.1, Spark,
  Image after-vision) blocked on free credits — video labels AFTER,
  never AFTER-VISION; matched-angle Donna AFTER needs a re-photo from
  the BEFORE angle only if the room is still tidy, never a re-clean;
  the filmed/edited video + voiceover itself.

## What the polish + demo-video stage needs

- Film/edit from `demo/DEMO_SCRIPT.md` (Diana approval checkboxes).
- When free credits land: SAM 3.1 / Spark / Image model ids + endpoints
  from the Meta Model API docs, then flip SPARK_ENABLED/IMAGE_ENABLED;
  the after-vision render feeds final_card() with after_kind="vision".
- APK routing (unchanged): APKs to Diana first in main chat; she
  forwards to Baymax.

## Polish + submission-prep — 2026-10-26 (DRAFT ONLY, nothing filed)

App polish pass on `app/lib/main.dart` (flow walked screen by screen;
every data key the UI reads re-verified against the real demo JSONs —
total_items, zones[].label/item_count, category_counts, quest_title,
est_minutes, energy_level, is_boss, steps[].id/instruction/
is_mystery_box, differ totals/zones/notes/angle_mismatch, sidecar
stats_text — all present):

- Stale-operation guard: Back on the scanning screen and Start-a-new-
  room mid-flight used to let the in-flight future yank the user to
  another screen on completion. Added an operation-generation counter
  (_opGen, bumped by _reset and by abandoning a scan); late
  completions and failures from abandoned operations are now ignored.
- Negative-differ dignity note: a pair like Donna's used to headline
  "Cleared -2 (-7.4%)" with no explanation. The numbers stay (honest),
  but a cleared < 0 pair now adds: different angles can move the
  count, no reduction is claimed from this pair, the card will use
  the plan numbers instead.
- Energy display: the quest screen showed the raw key ("an-hour");
  it now shows the human label ("An hour").
- Verified: `flutter analyze` clean; widget test passes; debug APK
  rebuilt (89M, MD5 990f239c8362a5ba2f64496c7361de5f — supersedes the
  14:18 build). APK routing unchanged: to Diana first, she forwards
  to Baymax. Proxy untouched (health ok, meta_api not-called,
  credits unconfirmed).

Submission draft package in `submission/` (DRAFT ONLY — nothing filed,
no video assembled — both need Diana/Damon):

- SUBMISSION_DRAFT.md — title, plain-language description, dignity-
  first framing, demo description, judging-criteria mapping (Impact
  30 / Meta AI Tooling 25 / Demo 25 / Technical Depth 20), and an
  explicit NOT-claiming section (no live SAM 3.1/Spark/Image results
  yet; no Donna reduction; no unblurred mirrors/faces; no
  AFTER-VISION label on a real photo).
- README.md — repo README: architecture, how to run, honest current
  state (local SAM 2.1 stand-in; Spark/Image coded-not-called until
  credits confirm), honest-numbers rules, privacy, watch items.
- CHECKLIST.md — what still needs a human: Diana's DEMO_SCRIPT
  approval + filming/editing; Damon's filing by Nov 4, 8:00 PM CT;
  credits check + possible live re-render; optional matched-angle
  Donna AFTER (BEFORE angle only, never a re-clean).
- AUDIT.md — every number in the draft re-verified against the demo
  sidecars (all PASS, incl. recomputed category sums 27 and 34);
  AFTER-VISION and 27 → 29 appear only in "never"/caveat contexts.
- screenshots/ — 5 privacy-processed demo visuals only (donna
  before-privacy, donna overlay, donna card, car overlay, car card);
  no raw photos in the folder (verified by filename scan).

Flagged, NOT changed (Diana's approval domain): DEMO_SCRIPT shot 6
says "when the angles do match, the proof is real" — but
car_differ.json records angle_mismatch: true for the car pair too.
Accurate line: the car's reduction is large enough to survive the
angle change (pile zones −100%). Fix at her approval pass.

Schemas untouched. $0 spent. NO Meta API calls. Sky and sourceoftruth
untouched.

## Debug-review fixes + BYOK + push prep — 2026-10-26 (Baymax review round)

Baymax's 2026-10-26 stage 1+2 debug review (1 blocker, 8 correctness
issues, 8 nits) worked through. $0 spent. NO Meta API calls. Schemas
untouched. Sky and sourceoftruth untouched. Proxy restarted on 8001
with the new code (health ok, meta_api: not-called).

VERIFIED (already closed by later work, confirmed in current code):
- B1 privacy, all four steps: mirror registry filled (Donna BEFORE 1
  box, Donna AFTER 2 boxes, car BEFORE 1 box); apply_privacy() called
  by card.compose_card() for both photos and by the proxy for every
  overlay; blurred outputs human-viewed (Donna card mirrors are blur
  washes, photographer unrecognizable); refusal is code (known-mirror
  photo without a registry entry raises, proxy surfaces it).
- C5 card_text: the share card now FORMATS the plan's card_text
  template (fallback to the same format string) -- no literal braces
  reach any card. The app UI never displayed card_text.
- C1 card-headline gate: proxy /card still requires raw>0 AND tidy>0
  for differ mode; the space_type source is now the explicit request
  field, not filename inference.

FIXED:
- C1 differ.py headline: differ output now carries headline {cleared,
  cleared_pct, before/after_tidy, basis} on the quest filter
  (structure always excluded, furniture in vehicle interiors); raw
  totals stay as secondary fields. Verified: car headline 26 (raw 23),
  Donna headline +1 (raw -2). Under angle-mismatch the note now says
  raw totals are NOT reliable across framings. CLI gains --space-type.
- C2 full-send surface reset: full-send plans always end with
  surfaces-s1; test_stage2 checks it.
- C3 Spark adapter: parse_response() unwraps the chat-completions
  envelope (choices[0].message.content JSON string); response_format
  is {"type": "json_object"}; model ids supplied from Baymax's verified
  live catalog: muse-spark-1.2 (default) / muse-spark-1.3, sam-3.1 for
  segmentation, muse-image-1.0 as the Image default. plan_with_spark()
  and generate_after_vision() take an api_key parameter (BYOK
  injection) but still refuse until credits confirm.
- C6 intro framing: intro keyed to the plan's actual minutes band
  ("up to an hour" for an 18-minute plan); rerouted plans recompute
  the intro from the new total (rest plan keeps its rest line).
- C8 trash: CLEAR_OUT_FIRST is [trash, paper, cardboard box];
  CATEGORY_PHRASES["trash"] is a throw-out action ("Throw out 2 bags
  of trash"), never "trashs".
- N1 space type explicit everywhere: detect_space_type() deleted from
  quest.py and card.py; plan()/differ()/card()/proxy/app take an
  explicit space_type (default "room"; vehicle must be declared). No
  filename-substring inference remains anywhere (grep-verified).
  proxy_test.py and test_stage3.py updated to pass space_type="vehicle"
  for the car pair (they had relied on the inference). Follow-up: the
  app has no space-type picker yet; live user photos default to room.
- N3 circuit breaker: run_stage1.py fails the whole labeling pass to
  heuristic after 3 consecutive VLM failures (single provenance,
  reported loudly).
- N4 validate.py: check() reports malformed inventories as errors --
  never a KeyError crash.
- N7 boss tie-break: among zones tied for largest tidy count, middle
  center wins when tied, else coverage, then zone_id. Car boss is now
  middle center (was middle right). NOTE: the pre-cached demo plan
  (stage2_car_quest.json) and submission draft still say middle right
  -- they predate this fix; a re-run regenerates. Flagged for the
  submission-prep pass; demo/ untouched (video source of truth).

CONNECTOR-NAME FINDING (NOT renamed): spark.py/image.py docstrings
said "custom.meta-llama-api" (vault connector created 2026-10-26 on
Remy's side); Baymax's review says the registered connector is
"custom.meta-model-api". No connector doc for either name exists on
file here, so neither can be confirmed from this side. Both docstrings
now record the discrepancy explicitly. Confirm against the vault
registration itself before the first live call.

BYOK config surface (DC's direction): app home screen gains a "Meta
Model API key (bring your own)" field -- EMPTY by default, obscured,
plus a setup note (paste your own key; it stays on the phone and is
sent only with your own requests; never baked into the app). api.dart
sends it as X-Meta-API-Key per request when set; the proxy holds it
request-scoped only (never logged, stored, or echoed) and passes it
as api_key= to the Spark/Image adapters when they enable. Pipeline
modules never read it from env or disk.

APK: rebuilt debug APK (flutter analyze clean, widget test passes)
with the BYOK field, delivered to Remy's parent as TidyQuest1.0.0.apk
for Diana to forward to Baymax per Damon's routing (NOT dropped in
to-baymax/ directly). Version 1.0.0 debug; debug key, sideload fine.

REPO PUSH PREP (NOT pushed): secret-scan of the whole scaffold found
no key/secret/token VALUES, no .env, no .pem/.key files -- only
api_key parameter names in code/docstrings. .gitignore added covering
keys/.env/secrets/test-assets/test_photos/test photos/out/demo/
submission screenshots/*.pt/.venv/APKs. No git repo initialized and
NOTHING pushed: verdict is READY FOR A SCOPED PUSH (code + docs +
schemas only, with the .gitignore) but NOT for a full-scaffold push --
out/, demo/, and submission screenshots/ are photo-bearing and stay
out unless Diana/Damon explicitly clear them, and the push itself
needs their word.

STILL OPEN: C4 live-tooling migration (needs credits); C7 labeling
quality (incl. "garbage"->bag keyword-order misfire, 0.7 placeholder
boundary); N2 budget step-order within a zone; N5 review-bundle
completeness; N6 semantic zone names; N8 segment.py ordering/centroid
notes; automatic angle-mismatch detection (flag still caller-passed;
the app exposes it as a checkbox).

Tests: local fix suite 25/25; luci test_stage2 ALL PASSED;
test_stage3 5/5; proxy_test ALL PASSED (car an-hour 9 quests /
20 steps / 22 min, card "26 items · 22 minutes"); flutter analyze
clean; widget test passes.

## 2026-10-26 (evening) -- stage-1 OOM fix (Diana live phone test)

Diana's full-resolution phone photo OOM'd SAM on the 4GB card (3:24 PM
CT: 415 MiB free; 3:39 PM retest: 159 MiB free, 1.17 GiB reserved by
PyTorch but unallocated = fragmentation on a full-res photo, not a
cross-run leak -- no run_stage1 process was lingering and the 4GB
card sat at 3,708 MiB free at check time; the proxy itself never
holds GPU memory, so a proxy restart is NOT the remedy here).
The app showed the raw traceback on the phone screen.

Fixes (all live on luci, proxy restarted on 8001, pid 3712293):
- run_stage1.py: downscale uploads to the 1536x2048 test-photo frame
  BEFORE SAM (LANCZOS). Test photos untouched, demo numbers stand.
  (Requested "1536 on the long edge" would shrink the test photos
  themselves -- frame rule used instead; flagged to Baymax.)
- segment.py: run_sam() returns (masks, device_used); CUDA OOM frees
  the model, empties the cache, retries on CPU. Non-OOM propagates.
  empty_cache() after every SAM pass, before labeling.
- run_stage1.py: PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
  setdefault before torch imports (fights the reserved-but-unallocated
  fragmentation the 3:39 trace showed).
- proxy.py: stage-1 runs serialized with a lock (ThreadingHTTPServer
  could otherwise run two SAM subprocesses at once and split the
  4GB card). Second scan queues, logged.
- proxy.py: Stage1Error type -- stage-1 failures return short human
  messages (OOM/timeout/generic variants); all other unexpected
  errors get a generic message; detail goes to the proxy stderr log
  only. No tracebacks or server paths reach the phone.
- pipeline/test_oom_fallback.py (new, mocked): 3/3 pass on luci.
- Inventory gains additive optional fields: sam_device, input_downscaled.

Verification (live through /inventory): untouched Donna BEFORE
(heuristic) = 27 items, cuda, not downscaled -- refactor neutral.
Upscaled Donna BEFORE 3000x4000: 3 sequential runs ALL HTTP 200
(50s/32s/34s; 28 items each -- resampled pixels, expected; cuda;
downscaled true; privacy-processed 1536x2048 overlay each time).
GPU free on the 4GB card 3,708 MiB before AND after every run --
zero degradation; 0 lingering run_stage1 processes. Junk upload
returns HTTP 500 with the short stage-1 message; traceback stays in
the server log. No app change needed -- the BYOK APK package stands.
Open: lock not contention-tested live (needs two simultaneous big
runs); C4/C7/N2/N5/N6/N8 unchanged from the re-review round.
