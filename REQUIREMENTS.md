# WatchDog — Requirements Specification

Single-user, self-hosted camera guard: ONVIF/RTSP cameras, detection zones, three
per-camera detection engines, face identification, webhooks, rolling live view.

Status: implemented (v0.1.x). This document is the source of truth for behaviour.

---

## 1. Scope

### 1.1 Goals

- Monitor an arbitrary number of LAN cameras (ONVIF or direct RTSP) from one web UI.
- Detect motion/humans **only inside a user-defined detection area (zone)**.
- Identify enrolled people (local CV, vision LLM, or hybrid) with per-person thresholds.
- Keep a rolling 5-minute live buffer per camera, viewable in the browser.
- Notify external systems via per-camera HTTP webhooks.
- Run as a single small Docker container with no cloud dependency (LLM optional/pluggable).

### 1.2 Non-goals

- Multi-user / roles / multi-tenant (one username+password for the whole instance).
- Cloud relay, mobile apps, push/e-mail notifications.
- Long-term recording/export (only the in-memory/on-disk HLS window is kept).
- PTZ control, audio, analytics beyond person/face detection.
- Multiple zones per camera (one polygon per camera).

### 1.3 Assumed user

A technical home/NAS user running Docker Compose on the same LAN as the cameras.

---

## 2. Deployment requirements

- **FR-DEP-1** Runs from a single image (`akashpanja/watchdog`) or built from source.
- **FR-DEP-2** Persistent state lives under `DATA_DIR` (`/data` in Docker): SQLite DB,
  reference photos, HLS segments, downloaded models.
- **FR-DEP-3** `APP_USERNAME` and `APP_PASSWORD` are mandatory; startup fails loudly
  if either is missing.
- **FR-DEP-4** One uvicorn worker only (streams and detection workers are in-process).
- **FR-DEP-5** ffmpeg must be available (`FFMPEG_BIN`, default `ffmpeg`).
- **FR-DEP-6** Models are downloaded lazily on first use into `DATA_DIR/models`:
  YOLOv8n (~6 MB), SCRFD `det_10g.onnx` + ArcFace `w600k_r50.onnx` (~306 MB total).
- **FR-DEP-7** Outbound network use is limited to LLM APIs (if configured) and webhooks.
- **FR-DEP-8** Image is published by CI on every `v*` tag and every push to `main`.

---

## 3. Functional requirements

### 3.1 Camera management

- **FR-CAM-1** Create/edit/delete cameras. Fields: name (required), detection engine,
  ONVIF host/port/username/password, optional RTSP URL, zone, webhook URL+secret.
- **FR-CAM-2** If no RTSP URL is supplied, the stream URI is resolved over ONVIF
  (`GetProfiles` → `GetStreamUri`, first profile). Resolution errors are returned to
  the UI as `ONVIF connection failed: <reason>`.
- **FR-CAM-3** When the camera returns an RTSP URI **without** userinfo, the ONVIF
  username/password are embedded into the URL (percent-encoded) because ffmpeg
  authenticates via the URL. Already-credentialed URLs are left untouched.
- **FR-CAM-4** `Test ONVIF` performs a real ONVIF round-trip and fills the RTSP field
  with the resolved URI; it must not require saving the camera first.
- **FR-CAM-5** ONVIF interaction must run inside the event loop
  (`onvif-zeep-async` creates an aiohttp session in its constructor); failures must
  surface as a readable message, never as a Python traceback.
- **FR-CAM-6** Editing a camera restarts its stream only when the URL/host/port changes;
  zone/mode/webhook changes apply live.
- **FR-CAM-7** Passwords are write-only in the API/UI: responses expose only
  `has_onvif_password` / `has_webhook_secret`.
- **FR-CAM-8** Optional per-camera motion overrides: `motion_threshold` (0.001–1) and
  `motion_cooldown` (0–3600 s). Unset means the global env default. They apply live and
  older databases are migrated in place on startup (columns added automatically).

### 3.2 ONVIF discovery

- **FR-DIS-1** `GET /api/cameras/discover` performs WS-Discovery (SOAP Probe for
  `NetworkVideoTransmitter`) to multicast `239.255.255.250:3702` **and** broadcast
  `255.255.255.255:3702`, collecting replies for `timeout` seconds (default 3, clamp 0.5–15).
- **FR-DIS-2** Each result: `host`, `onvif_port` (from XAddrs), `name`, `hardware`
  (from ONVIF scopes), `xaddr`. IPv6 literals and duplicates are dropped.
- **FR-DIS-3** UI: `Discover ONVIF` button in the camera dialog lists devices; clicking
  one fills host+port.

### 3.3 Streaming & live view

- **FR-STR-1** Each camera keeps a rolling HLS window of `HLS_WINDOW_SECONDS` (default
  300 s) with `HLS_TIME`-second segments (default 2), segment cleanup enabled, and
  independent segments so seeking works.
- **FR-STR-2** **Exactly one ffmpeg process / one RTSP connection per camera**, producing
  both the HLS playlist and the analysis-frame pipe. Rationale: many consumer cameras
  allow only one RTSP viewer per stream.
- **FR-STR-3** Analysis frames: MJPEG over stdout, `ANALYSIS_FPS` (default 2) and width
  capped at `ANALYSIS_WIDTH` (default 1280); the newest frame is exposed as
  `GET /api/cameras/{id}/snapshot.jpg`.
- **FR-STR-4** Stream supervision: if ffmpeg exits, restart after a 2 s back-off,
  indefinitely, without crashing the app; stale frames older than `FRAME_MAX_AGE`
  (default 6 s) are ignored by detection. A stall watchdog also restarts ffmpeg when a
  live process produces no frames for `STREAM_STALL_SECONDS` (default 30, 0 disables),
  which covers RTSP connections that freeze without closing (camera reboot/Wi-Fi loss).
- **FR-STR-5** HLS is copied (no re-encode) by default; `HLS_TRANSCODE=true` switches the
  playlist to libx264/2500k for cameras whose codec browsers cannot play (e.g. HEVC).
- **FR-STR-6** The UI live view uses hls.js, keeps the last 5 minutes seekable, and shows
  a "stream error — retrying" state while the supervisor recovers.
- **FR-STR-7** The analysis frame rate is independent of the playback stream; playback
  must not be degraded by detection settings.
- **FR-STR-8** Camera viewer limits are respected: one viewer per stream is the
  documented baseline (see §7), so the app must not open additional connections to the
  same URL (no second ffmpeg, no per-request ffmpeg probing).
- **FR-STR-9** The playlist must be playable both from `…/stream.m3u8` and
  `…/hls/index.m3u8`; playlist-relative segment URLs (resolved next to the playlist) are
  served so players that do not rewrite the base URL still work.

### 3.4 Detection area (zone)

- **FR-ZON-1** One zone per camera, a closed polygon of **3–50 points** with normalized
  coordinates in `[0,1]` (relative to the analysis frame).
- **FR-ZON-2** An **empty zone means "whole frame"** — detection applies everywhere.
- **FR-ZON-3** Invalid polygons (fewer than 3 points, out-of-range values) are rejected
  with HTTP 400 on `PUT /api/cameras/{id}/zone`.
- **FR-ZON-4** Zone editing UI: `Edit zone` shows the snapshot, user clicks to add points,
  double-click ends drawing, `Save zone` persists. Minimum 3 points enforced client-side
  and server-side.
- **FR-ZON-5** Motion detection only counts pixels **inside** the zone: grayscale
  absolute difference (Gaussian blur 21×21, threshold 25) intersected with the zone mask;
  ratio = changed-in-zone / zone-area.
- **FR-ZON-6** Human detection only counts persons **inside** the zone: a YOLO person box
  counts when its centre **or any corner** falls inside the polygon.
- **FR-ZON-7** In `llm` mode the zone is drawn as a yellow polygon on the frame given to
  the model; the prompt explicitly asks whether a human is inside the yellow area.
- **FR-ZON-8** Zone changes take effect immediately (live worker update), without
  restarting ffmpeg.

### 3.5 Detection engines (per camera)

- **FR-ENG-1** Engine is chosen per camera: `python`, `llm`, `hybrid`.
- **FR-ENG-2** `python`: fully local — YOLOv8n person detection in the zone, SCRFD face
  detection + ArcFace embeddings for identification. Requires onnxruntime; no cloud calls.
- **FR-ENG-3** `llm`: human presence and identification decided by a vision-capable LLM
  configured globally in Settings. No local models required.
- **FR-ENG-4** `hybrid`: run the local result first; escalate to the LLM only when the
  local verdict is uncertain — no human detected, no name, no threshold, or
  confidence < (person threshold − 10). The higher-confidence verdict wins; the returned
  `source` states `python` or `llm`. LLM failure falls back to the local verdict.
- **FR-ENG-5** Engine selection is validated: `llm`/`hybrid` require LLM settings to be
  present, otherwise the request is rejected.
- **FR-ENG-6** Switching engine does not require re-adding the camera.

### 3.6 Motion & human detection

- **FR-MOT-1** Motion is computed on a downscaled grayscale frame (320 px wide) against the
  previous frame, masked to the zone.
- **FR-MOT-2** A trigger fires when ratio ≥ the effective threshold (per-camera
  `motion_threshold`, else `MOTION_THRESHOLD` default 0.02 = 2 %) and at least the
  effective cooldown (per-camera `motion_cooldown`, else `MOTION_COOLDOWN` default 15 s)
  passed since the last trigger.
- **FR-MOT-3** Every trigger emits a `motion` event (webhook + event log) with confidence
  = ratio × 100 and a base64 JPEG snapshot of the triggering frame.
- **FR-MOT-4** A `human` event follows when the engine decides a person is inside the zone:
  - `python`/`hybrid`: any person box in the zone, confidence = best YOLO score ×100;
  - `llm`: model verdict `person_found`, confidence as reported (0–100).
- **FR-MOT-5** Trigger pipeline (all engines, used for the LLM/face stage):
  1. keep a ring buffer of the last `FRAME_BUFFER_FRAMES` (default 15) analysis frames;
  2. on trigger, scan the newest 6 buffered frames for faces;
  3. pick the best face by **area × detector score**;
  4. crop with 40 % padding and upscale so the shorter side ≥ `FACE_CROP_SIZE`
     (default 512 px), JPEG q90;
  5. send that cropped/zoomed face (plus enrolled references for identify) to the LLM —
     not the full frame;
  6. fallback: upper 45 % of the best person box (head crop); for the human verdict, no
     person means "not present" and no image is sent; for identification, if neither a
     face nor a person is found, the newest frame is sent so a verdict is still possible.

### 3.7 People enrollment

- **FR-PER-1** Create/edit/delete people; name is required and unique.
- **FR-PER-2** Per-person match threshold, default **95**, range 0–100.
- **FR-PER-3** Upload up to **20** photos per person (JPEG/PNG); invalid/empty images are
  rejected; photos are stored under `DATA_DIR/faces/<person>/`.
- **FR-PER-4** Photos can be deleted individually; deleting a person removes their photos.
- **FR-PER-5** Photos are served to the authenticated UI only.

### 3.8 Identification

- **FR-ID-1** `POST /api/cameras/{id}/identify` identifies the person on that camera using
  the newest buffered frames (blocking call, single result).
- **FR-ID-2** Local matching: SCRFD → ArcFace embedding → cosine similarity against the
  gallery; `confidence = 100 × sigmoid((similarity − 0.55) / 0.06)`; best person wins.
- **FR-ID-3** `match = confidence ≥ person.threshold` (per-person, default 95 ≈ similarity 0.73).
- **FR-ID-4** LLM matching: references (up to **8** photos, first photo per person) plus one
  live image — the zoomed face crop when a face is isolated, else the head crop of the
  closest person, else the newest frame (shrunk) — so the model never receives references
  without a live image. The returned name must be an **enrolled name**, otherwise treated
  as no match (hallucination guard); `match` requires the person's threshold to be met.
- **FR-ID-5** Response: `match`, `name`, `confidence`, `threshold_used`, `human_detected`,
  `face_crop_b64` (preview of the exact crop used), `source` (`python`/`llm`).
- **FR-ID-6** Identification failures degrade gracefully (no match + reason logged), never
  a 500 crash.
- **FR-ID-7** The UI shows match/name/confidence/source/threshold and the cropped face.

### 3.9 Webhooks & event log

- **FR-WH-1** Per-camera webhook URL + optional shared secret (sent as `X-WatchDog-Secret`).
- **FR-WH-2** Payload: `event` (`motion`|`human`), `camera_id`, `camera_name`,
  `timestamp` (ISO-8601 UTC), `confidence`, `snapshot_b64` (JPEG, may be null).
- **FR-WH-3** Delivery timeout `WEBHOOK_TIMEOUT` (default 10 s); retries transient
  failures up to `WEBHOOK_RETRIES` times (default 2; only network errors, 429 and 5xx —
  other 4xx are final) with a short exponential back-off. Failures are logged and stored
  in the event detail (including the attempt count) and never abort detection.
- **FR-WH-4** Every event is logged in SQLite with delivery status; the newest **200**
  events are retained.
- **FR-WH-5** Retries run inline in the camera's detection thread, so a dead receiver can
  delay that camera's next detection by up to the retry budget (bounded by 2 s per wait).

### 3.10 Settings & LLM

- **FR-LLM-1** Global LLM settings shared by all `llm`/`hybrid` cameras: provider, API key,
  model, base URL (optional).
- **FR-LLM-2** Providers: `openai`, `openai_compatible`, `claude`, `groq`, `openrouter`.
- **FR-LLM-3** Defaults: OpenAI `gpt-4o-mini`, Claude `claude-sonnet-4-5`,
  Groq `meta-llama/llama-4-scout-17b-16e-instruct`,
  OpenRouter `google/gemini-2.5-flash`.
- **FR-LLM-4** The model **must accept image inputs**; `Test` performs a real request and
  reports the failure verbatim.
- **FR-LLM-5** LLM responses are parsed as strict JSON; non-JSON or invalid verdicts are
  treated as errors (never as a match).
- **FR-LLM-6** Requests time out after `LLM_TIMEOUT` (default 60 s).

### 3.11 Authentication & security

- **FR-AUTH-1** Single user from `APP_USERNAME`/`APP_PASSWORD`; session cookie signed with
  `itsdangerous` (`SESSION_SECRET`, derived from credentials if unset).
- **FR-AUTH-2** Cookie lifetime `SESSION_MAX_AGE` (default 7 days); `COOKIE_SECURE=true`
  for HTTPS deployments.
- **FR-AUTH-3** All `/api/*` routes require auth **except** `/api/login` and `/api/health`.
- **FR-AUTH-4** Login compares username and password; failures return 401 without revealing
  which field was wrong.
- **FR-AUTH-5** Container runs with `no-new-privileges`; no shell/exec endpoints exist.
- **FR-AUTH-6** Secrets (ONVIF/webhook passwords, LLM keys) are never returned in API
  responses; LLM keys are write-only.
- **FR-AUTH-7** Threat model: single-user LAN deployment; credentials are stored locally
  (not hashed) — the DB and env must be protected by the host.
- **FR-AUTH-8** Failed logins are rate-limited per client IP: after
  `LOGIN_MAX_ATTEMPTS` (default 10) failures inside `LOGIN_WINDOW_SECONDS` (default 300),
  logins return **429** with `Retry-After` until the window expires. A successful login
  clears the counter.

### 3.12 UI

- **FR-UI-1** Single-page app (no build step) with: Cameras, Persons, Events, Settings.
- **FR-UI-2** Camera card: live HLS player with 5-minute seek, zone editor, webhook dialog,
  Identify button + result, edit/delete.
- **FR-UI-3** Add/Edit camera dialog: name, engine, ONVIF host/port/user/password,
  RTSP URL, Discover ONVIF, Test ONVIF, Save/Cancel, inline errors.
- **FR-UI-4** Persons: add/edit, threshold slider/field, photo upload (multiple), thumbnails,
  delete photo/person.
- **FR-UI-5** Events page lists recent events (newest first) with camera, kind, confidence,
  webhook status and timestamp.
- **FR-UI-6** Settings: provider, model, key, base URL, Test.
- **FR-UI-7** UI is usable on desktop browsers; no mobile-specific layout requirement.

### 3.13 Persistence & retention

- **FR-DATA-1** SQLite at `DATA_DIR/watchdog.db` (cameras, persons, photos, settings, events).
- **FR-DATA-2** HLS segments per camera under `DATA_DIR/hls/<camera_id>/`, pruned by the
  playlist window and deleted on camera deletion/stream restart.
- **FR-DATA-3** Reference photos under `DATA_DIR/faces/<person>/`.
- **FR-DATA-4** No data leaves the host except webhook payloads and LLM requests.

---

## 4. API surface (all JSON, cookie auth, prefix `/api`)

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/login` | Sign in, sets session cookie (public) |
| POST | `/logout` | Clear session |
| GET | `/me` | Current user |
| GET | `/health` | Liveness (public) |
| GET | `/cameras` | List cameras |
| POST | `/cameras` | Create camera (name, mode, host, port, onvif_username, onvif_password, rtsp_url, motion_threshold?, motion_cooldown?) |
| PUT | `/cameras/{id}` | Update camera |
| DELETE | `/cameras/{id}` | Delete camera + its HLS data |
| GET | `/cameras/discover` | WS-Discovery probe (`?timeout=3`) |
| POST | `/cameras/test-onvif` | Resolve RTSP via ONVIF → `{rtsp_url}` |
| GET | `/cameras/{id}/stream.m3u8` | Live HLS playlist |
| GET | `/cameras/{id}/hls/{segment}` | HLS segment |
| GET | `/cameras/{id}/snapshot.jpg` | Newest analysis frame |
| PUT | `/cameras/{id}/zone` | `{polygon: [[x,y],…]}` normalized |
| PUT | `/cameras/{id}/webhook` | `{url, secret}` |
| POST | `/cameras/{id}/identify` | Identify person now |
| GET | `/persons` | List people |
| POST | `/persons` | Create person `{name, threshold}` |
| PUT | `/persons/{id}` | Update person |
| DELETE | `/persons/{id}` | Delete person + photos |
| POST | `/persons/{id}/photos` | Multipart upload (≤20 total) |
| GET | `/persons/{id}/photos/{photo_id}` | Photo bytes |
| DELETE | `/persons/{id}/photos/{photo_id}` | Delete photo |
| GET | `/events` | Recent events (≤200) |
| GET | `/settings` | LLM settings (key redacted) |
| PUT | `/settings` | Update LLM settings |
| POST | `/settings/test` | Real LLM request |

---

## 5. Configuration reference

| Variable | Default | Meaning |
| --- | --- | --- |
| `APP_USERNAME` | *(required)* | Instance username |
| `APP_PASSWORD` | *(required)* | Instance password |
| `SESSION_SECRET` | derived | Cookie signing key |
| `SESSION_MAX_AGE` | `604800` | Session lifetime (s) |
| `COOKIE_SECURE` | `false` | Set Secure cookie (HTTPS only) |
| `DATA_DIR` | `/data` | Persistent state root |
| `FFMPEG_BIN` | `ffmpeg` | ffmpeg executable |
| `HLS_TIME` | `2` | Segment length (s) |
| `HLS_WINDOW_SECONDS` | `300` | Rolling buffer length (s) |
| `HLS_TRANSCODE` | `false` | Re-encode HLS with libx264 (HEVC/browser fixes) |
| `ANALYSIS_FPS` | `2` | Frames/s for detection |
| `ANALYSIS_WIDTH` | `1280` | Max analysis frame width |
| `STREAM_STALL_SECONDS` | `30` | Restart ffmpeg if no frames arrive (0 disables) |
| `FRAME_BUFFER_FRAMES` | `15` | Ring buffer size for triggers |
| `FRAME_MAX_AGE` | `6` | Ignore frames older than this (s) |
| `FACE_CROP_SIZE` | `512` | Minimum face-crop side after zoom |
| `MOTION_THRESHOLD` | `0.02` | Motion ratio to trigger |
| `MOTION_COOLDOWN` | `15` | Seconds between triggers |
| `WEBHOOK_TIMEOUT` | `10` | Webhook timeout (s) |
| `WEBHOOK_RETRIES` | `2` | Retries for transient webhook failures |
| `LOGIN_MAX_ATTEMPTS` | `10` | Failed logins per IP before lockout |
| `LOGIN_WINDOW_SECONDS` | `300` | Failure-counting window / lockout duration |
| `LLM_TIMEOUT` | `60` | LLM request timeout (s) |
| `WATCHDOG_PORT` | `8099` | Published host port (compose) |
| `TZ` | `UTC` | Container timezone |

---

## 6. Non-functional requirements

- **NFR-1** Runs on x86_64 CPU only; no GPU required. Python 3.12 image.
- **NFR-2** Startup < 10 s; detection overhead ≤ ~10 % CPU per camera at defaults
  (2 fps analysis, YOLOv8n).
- **NFR-3** Memory: bounded HLS window and 15-frame buffer per camera; event log capped at 200.
- **NFR-4** Crash-resilience: a dead camera/stream/LLM/webhook must never take down the app.
- **NFR-5** All logs to stderr with standard levels; no secrets in logs.
- **NFR-6** Repo is lint-clean (`ruff`) and unit-tested (`pytest`): 43 tests, 1 skipped
  when local vision deps are absent. CI builds/publishes the image.
- **NFR-7** Time handling: UTC everywhere internally; ISO-8601 in payloads; `TZ` only
  affects container logs.

---

## 7. Constraints & known limitations

- **L-1 One RTSP viewer per stream** on many consumer cameras (verified: Tapo C320WS).
  The app uses exactly one connection; a second viewer (e.g. ODM/VLC) on the same
  stream/path will knock the app (or itself) off. Use the substream (`/stream2`) for
  coexistence.
- **L-2** Substreams are lower resolution; face quality (and LLM accuracy) is better on the
  main stream, which then must be dedicated to WatchDog.
- **L-3** `python`/`hybrid` local stages need onnxruntime — unavailable on musl/Alpine
  builds (dev-only caveat); the Docker image (Debian) includes it.
- **L-4** HEVC/H.265 streams require `HLS_TRANSCODE=true` for browser playback.
- **L-5** Audio is ignored (`-an`); no two-way audio.
- **L-6** ONVIF password and webhook secret are stored in SQLite (single-user local threat
  model); LLM keys are stored server-side and never returned.
- **L-7** Identification is threshold-based; per-person threshold must be tuned per camera
  distance/lighting.
- **L-8** Single uvicorn worker is mandatory; multiple workers would duplicate streams.
- **L-9** Login throttling is in-memory and per-process (resets on restart) and keys on the
  direct client address; deploy behind a trusted proxy only if you accept that.

---

## 8. Acceptance criteria

1. Adding a camera via Discover + Test ONVIF + Save yields a playing live view within ~10 s.
2. With a zone set, motion outside the zone triggers nothing; motion inside triggers a
   `motion` event with snapshot and confidence.
3. Engine `python`: person inside the zone produces a `human` event; outside the zone does not.
4. Engine `llm`: the model receives the zoomed face crop (or head/full-frame fallback when
   no face is isolated) and returns a validated verdict; invalid JSON → no match, logged
   error.
5. `hybrid` escalates only on uncertain local results and reports `source`.
6. Identifying an enrolled person returns `match=true`, correct name, confidence ≥ their
   threshold, and a face preview; unknown faces return `match=false`.
7. Uploading >20 photos is rejected; deleting a person deletes photos.
8. Webhook receiver gets the documented JSON with `X-WatchDog-Secret`; delivery failure is
   visible in the event log but detection continues.
9. Killing ffmpeg or unplugging the camera shows "retrying" and recovers automatically
   without restarting the container.
10. All `/api/*` calls except health/login require a session cookie.
11. A per-camera `motion_threshold` override changes sensitivity immediately while the
    global default still applies to cameras without overrides; an upgraded database gains
    the new columns on startup.
12. A receiver returning 503 twice then 200 gets one webhook call after retries, with
    `attempts=3` recorded on the event; a 400 is not retried.
13. After repeated bad passwords, login returns 429 with `Retry-After`; after the window
    (or a restart) login works again.
14. If the RTSP feed freezes without closing, the stall watchdog restarts ffmpeg within
    ~`STREAM_STALL_SECONDS` + 5 s and the playlist advances again.
15. A player loading `/stream.m3u8` fetches `seg_*.ts` next to it successfully (no 404s).

---

## 9. Verification

```bash
# unit tests (no camera needed)
pytest -q
# lint
ruff check app tests
# syntax
python -m compileall -q app
```

Live smoke (with credentials): `GET /api/health`, login, `GET /api/cameras`,
`GET /api/cameras/{id}/stream.m3u8` (expect `#EXTM3U`), `GET /api/cameras/{id}/snapshot.jpg`
(expect JPEG magic `FF D8`).
