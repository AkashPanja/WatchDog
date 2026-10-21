# WatchDog

Self-hosted, single-user camera guard in Docker: live ONVIF/RTSP viewing with a rolling
5-minute buffer, canvas-marked detection zones, webhooks for motion/human events, an
enrolled-people gallery, and an identification API that returns a match verdict, name and
0–100 confidence score.

Full behaviour specification: [REQUIREMENTS.md](REQUIREMENTS.md).

Each camera independently chooses its detection engine:

| Engine | Motion | Human in zone | Identification |
| --- | --- | --- | --- |
| `python` | OpenCV frame diff | YOLOv8n ONNX inside the zone | SCRFD face detect → crop/zoom → ArcFace embedding match (local, offline) |
| `llm` | OpenCV frame diff | Vision LLM asks "person in the yellow zone?" (zoomed best-face/head crop) | Vision LLM compares the zoomed live face with enrolled reference photos |
| `hybrid` | OpenCV frame diff | YOLOv8n (cheap gate) | Local face match first; escalates to the LLM only when uncertain |

LLM providers: **OpenAI**, **OpenAI-compatible** (LM Studio, vLLM, DeepSeek, Mistral, …),
**Claude**, **Groq**, **OpenRouter**. Models must accept images.

## Quick start

```bash
cp .env.example .env      # set APP_USERNAME and APP_PASSWORD
docker compose up -d
```

The compose file pulls the prebuilt image (`akashpanja/watchdog:latest`) from Docker Hub —
no compiler or build tools needed. Open `http://<host>:8099` and sign in with the
credentials from `.env`.

Ports and credentials are all environment-driven (see below). Everything persistent lives
in `./data`: database, face photos, downloaded models and the HLS buffer.

To build from source instead, comment out `image:` in `docker-compose.yml`, uncomment
`build: .`, and run `docker compose up -d --build`.

## Prebuilt image (Docker Hub)

Published images: [`akashpanja/watchdog`](https://hub.docker.com/r/akashpanja/watchdog)
(`:latest`, plus `:0.1.0` / `:0.1` version tags).

CI builds and pushes the image automatically on every `v*` tag and every push to `main`
(workflow: `.github/workflows/docker-publish.yml`). Maintainer release flow:

1. On Docker Hub: Account settings → Personal access tokens → Generate (Read & Write).
2. On GitHub: repo → Settings → Secrets and variables → Actions → New repository secret:
   - `DOCKERHUB_USERNAME` — Docker Hub username (`akashpanja`)
   - `DOCKERHUB_TOKEN` — the token from step 1
3. Push a tag to publish: `git tag v0.1.0 && git push origin v0.1.0`

## Environment

| Variable | Default | Description |
| --- | --- | --- |
| `APP_USERNAME` | *(required)* | The single UI/API user |
| `APP_PASSWORD` | *(required)* | Password for that user |
| `SESSION_SECRET` | derived | Cookie signing key; set a random value for stable sessions |
| `COOKIE_SECURE` | `false` | Set `true` when served over HTTPS |
| `WATCHDOG_PORT` | `8099` | Host port mapping |
| `DATA_DIR` | `/data` | Storage inside the container |
| `HLS_TIME` | `2` | Segment length (seconds) |
| `HLS_WINDOW_SECONDS` | `300` | Rolling buffer length (5 minutes), kept for **every** camera regardless of engine |
| `HLS_TRANSCODE` | `false` | `true` re-encodes to H.264 (use for HEVC cameras or broken `-c:v copy` streams) |
| `ANALYSIS_FPS` | `2` | Frames per second pulled for detection/identification |
| `ANALYSIS_WIDTH` | `1280` | Width of analysis frames |
| `FRAME_BUFFER_FRAMES` | `15` | Analysis frames kept in memory and scanned on a trigger |
| `FACE_CROP_SIZE` | `512` | Minimum size the cropped/zoomed face is enlarged to before it goes to the LLM |
| `MOTION_THRESHOLD` | `0.02` | Default fraction of zone pixels changed to count as motion (per-camera override in the camera dialog) |
| `MOTION_COOLDOWN` | `15` | Default seconds between motion webhooks (per-camera override in the camera dialog) |
| `WEBHOOK_TIMEOUT` | `10` | Webhook HTTP timeout |
| `WEBHOOK_RETRIES` | `2` | Retries for transient webhook failures (5xx/429/network; 4xx is final) |
| `LOGIN_MAX_ATTEMPTS` | `10` | Failed logins per client IP before a temporary lockout |
| `LOGIN_WINDOW_SECONDS` | `300` | Window for counting failed logins / lockout duration |
| `LLM_TIMEOUT` | `60` | LLM request timeout |
| `TZ` | `UTC` | Container timezone |

## Using the UI

**Cameras**
1. **Add camera** → name, detection engine, ONVIF host/port/username/password
   (port is usually 80, some devices use 8000) or paste an RTSP URL directly.
   *Discover ONVIF* probes the LAN (WS-Discovery) and lists found cameras —
   click one to fill in host/port. *Test ONVIF* resolves the stream URI and fills
   the RTSP field.
2. The live view uses HLS with a rolling 5-minute playlist — drag the seek bar to review
   the last 5 minutes; older segments are deleted automatically.
3. **Edit zone** → click points on the video (min 3) → **Save zone**. With no zone the
   whole frame counts. Double-click ends drawing.
   Motion **threshold** and **cooldown** can be overridden per camera in the same dialog
   (blank = the `MOTION_*` environment defaults); changes apply without a stream restart.
4. **Webhook** → URL plus optional shared secret (sent as `X-WatchDog-Secret`);
   transient failures are retried (`WEBHOOK_RETRIES`), final status is stored on the event.
5. **Identify** → runs identification for that camera and shows match, name, confidence,
   source, threshold and a cropped face image.

**People**
- Add a person with a name and a per-person match threshold (default **95**).
- Upload several photos (up to 20) — different angles/lighting improve accuracy.
- The threshold is applied to the returned confidence: `match = confidence >= threshold`.

**Settings** — LLM provider/key/model/base URL, shared by all `llm`/`hybrid` cameras.
*Test* performs a real request so misconfiguration surfaces immediately.

## Identification API

Authenticate (cookie is then used for all calls):

```bash
curl -c cookies.txt -X POST http://localhost:8099/api/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"change-me-please"}'

curl -b cookies.txt -X POST http://localhost:8099/api/cameras/<CAMERA_ID>/identify
```

Response:

```json
{
  "match": true,
  "name": "Alice",
  "confidence": 97.3,
  "threshold_used": 95.0,
  "human_detected": true,
  "face_crop_b64": "<base64 jpeg of the cropped/zoomed face>",
  "source": "python",
  "similarity": 0.7512
}
```

- `match` is `confidence >= threshold_used` (per-person threshold; default 95).
- `source` is `python` or `llm` (hybrid reports whichever produced the returned verdict).
- In `llm` and `hybrid` modes the LLM receives only the cropped/zoomed face (falling back
  to a head crop, then the newest frame when no face can be isolated), so
  `face_crop_b64` is returned for every engine.
- A returned LLM name that is not enrolled is discarded as a non-match.
- The rolling 5-minute buffer is recorded for every camera, whatever engine it uses.

Other endpoints (all JSON unless noted):

```
POST   /api/login                      {username, password} → session cookie
POST   /api/logout
GET    /api/me

GET    /api/cameras                    list cameras
POST   /api/cameras                    {name, mode, host, port, onvif_username,
                                        onvif_password, rtsp_url?,
                                        motion_threshold?, motion_cooldown?}
PUT    /api/cameras/{id}
DELETE /api/cameras/{id}
POST   /api/cameras/test-onvif         {host, port, username, password} → {rtsp_url}
GET    /api/cameras/discover           WS-Discovery probe → [{host, onvif_port, name, hardware}]
GET    /api/cameras/{id}/stream.m3u8   live HLS playlist (5-minute window)
GET    /api/cameras/{id}/snapshot.jpg  newest frame
PUT    /api/cameras/{id}/zone          {polygon: [[x,y],...]} normalized 0-1
PUT    /api/cameras/{id}/webhook       {url, secret}
POST   /api/cameras/{id}/identify

GET    /api/persons                    list people with photo ids
POST   /api/persons                    {name, threshold}
PUT    /api/persons/{id}               {name?, threshold?}
DELETE /api/persons/{id}
POST   /api/persons/{id}/photos        multipart "files" (multiple allowed)
GET    /api/persons/{id}/photos/{pid}
DELETE /api/persons/{id}/photos/{pid}

GET    /api/settings
PUT    /api/settings                   {provider, api_key?, model, base_url}
POST   /api/settings/test

GET    /api/events?limit=50
```

## Webhook payload

Fired on motion inside the zone (`motion`) and on a detected person (`human`):

```json
{
  "event": "human",
  "camera_id": "9f2c...",
  "camera_name": "Front door",
  "timestamp": "2026-10-20T12:00:00+00:00",
  "confidence": 91.4,
  "snapshot_b64": "<base64 jpeg snapshot, 640px wide, or null>"
}
```

Header `X-WatchDog-Secret: <secret>` is sent when a secret is configured. Delivery results
are visible in **Events**.

## How it works

- **ONVIF**: the ONVIF media service returns the RTSP URI; ffmpeg consumes it.
- **Streaming**: one ffmpeg writes HLS segments (`hls_list_size = 300s / segment length`),
  a second ffmpeg emits 2 fps JPEG frames for detection and identification.
  Only the last 5 minutes per camera are kept on disk.
- **Motion**: grayscale diff masked by the zone polygon; ratio above the threshold triggers.
- **Trigger pipeline**: when a trigger fires (motion/human or `identify`), the buffered
  analysis frames are scanned, the clearest/largest face is picked, cropped and zoomed
  (padded, upscaled to `FACE_CROP_SIZE`) and sent to the LLM together with the enrolled
  references — normally only the zoomed face, with a head crop then the newest frame as
  fallbacks when no face is isolated — which gives noticeably more accurate verdicts.
- **People (python/hybrid)**: YOLOv8n ONNX person boxes, bbox center/corners tested against
  the zone polygon.
- **Faces**: SCRFD (`det_10g.onnx`) detection, 5-point alignment, ArcFace (`w600k_r50.onnx`)
  512-d embeddings. Confidence is a calibrated sigmoid of cosine similarity
  (`50` at similarity `0.55`, `~95` at `~0.73`), not the raw cosine.
- **Models** download on first use into `data/models` (~300 MB for the face pack,
  ~12 MB for YOLO) — the container needs outbound internet once.
- **Single process**: streams and models live in memory, so run exactly one uvicorn worker
  (the provided image already does).

## Notes and limitations

- HLS adds roughly 3–8 seconds of latency; seeking back within the window is instant.
- `-c:v copy` keeps CPU low but requires H.264 in the camera stream; set
  `HLS_TRANSCODE=true` if the live view stays empty (e.g. HEVC cameras).
- ONVIF credentials are stored in the local SQLite database — protect the `data/` volume.
- Put the app behind a TLS reverse proxy and set `COOKIE_SECURE=true` if exposed beyond
  your LAN.
- The local engine is CPU-only (onnxruntime); expect ~0.1–0.5 s per identify on a small
  VM, LLM latency depends on your provider.
- A person's threshold of 95 is strict; lower it (e.g. 85) if genuine matches score below
  it, or enroll clearer photos.

## Development

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
DATA_DIR=./data APP_USERNAME=admin APP_PASSWORD=admin uvicorn app.main:app --reload
pytest -q
```

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `stream is starting, retry shortly` | ONVIF/RTSP URL unreachable or ffmpeg still connecting; check credentials and codec |
| Live view black, Events still fire | Codec not browser-safe (HEVC) → `HLS_TRANSCODE=true` |
| `ONVIF connection failed` | Wrong host/port/credentials, or ONVIF disabled on the camera |
| `configure an LLM API key...` | Set the provider/key/model in Settings before choosing llm/hybrid |
| LLM 400/401 | Wrong key/model, or model lacks image support |
| `camera frame is stale` | Stream stopped; check logs (`docker compose logs -f`) |
| Person never matches | Add clearer photos, check `threshold`, watch `confidence` in the identify response |
