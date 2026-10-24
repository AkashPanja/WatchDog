"use strict";

const state = {
  cameras: [],
  persons: [],
  settings: null,
  hls: [],
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));

function toast(message, bad = false) {
  const el = $("#toast");
  el.textContent = message;
  el.classList.toggle("bad", bad);
  el.classList.remove("hidden");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => el.classList.add("hidden"), 3500);
}

async function api(path, options = {}) {
  const init = { credentials: "same-origin", ...options };
  if (init.body && !(init.body instanceof FormData)) {
    init.headers = { "Content-Type": "application/json", ...(init.headers || {}) };
    if (typeof init.body !== "string") init.body = JSON.stringify(init.body);
  }
  const response = await fetch(path, init);
  if (response.status === 401) {
    showLogin();
    throw new Error("unauthorized");
  }
  let payload = null;
  const text = await response.text();
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = { detail: text };
    }
  }
  if (!response.ok) {
    throw new Error((payload && payload.detail) || response.statusText);
  }
  return payload;
}

function showLogin() {
  $("#app").classList.add("hidden");
  $("#login").classList.remove("hidden");
}

function showApp() {
  $("#login").classList.add("hidden");
  $("#app").classList.remove("hidden");
}

async function boot() {
  try {
    await api("/api/me");
    showApp();
    switchTab("cameras");
  } catch {
    showLogin();
  }
}

/* ------------------------------- tabs ------------------------------- */

function switchTab(name) {
  $$("#tabs button").forEach((button) => {
    button.classList.toggle("active", button.dataset.tab === name);
  });
  ["cameras", "persons", "events", "settings"].forEach((tab) => {
    $(`#view-${tab}`).classList.toggle("hidden", tab !== name);
  });
  if (name === "cameras") loadCameras();
  if (name === "persons") loadPersons();
  if (name === "events") loadEvents();
  if (name === "settings") loadSettings();
}

/* ------------------------------ cameras ------------------------------ */

function destroyHls() {
  state.hls.forEach((instance) => {
    try {
      instance.destroy();
    } catch {}
  });
  state.hls = [];
}

async function loadCameras() {
  try {
    state.cameras = await api("/api/cameras");
  } catch (err) {
    toast(err.message, true);
    return;
  }
  renderCameras();
}

function renderCameras() {
  destroyHls();
  const grid = $("#camera-grid");
  grid.innerHTML = "";
  $("#cameras-empty").classList.toggle("hidden", state.cameras.length > 0);

  for (const camera of state.cameras) {
    const card = document.createElement("div");
    card.className = "card camera-card";
    card.innerHTML = `
      <div class="camera-head">
        <h3></h3>
        <span class="badge ${camera.mode}">${camera.mode}</span>
      </div>
      <div class="video-wrap" data-id="${camera.id}">
        <video muted autoplay playsinline controls></video>
        <canvas></canvas>
        <span class="video-status">live · 5 min buffer</span>
      </div>
      <div class="camera-actions">
        <button class="primary small" data-act="identify">Identify</button>
        <button class="small" data-act="zone">Edit zone</button>
        <button class="small" data-act="webhook">Webhook</button>
        <button class="small" data-act="edit">Edit</button>
        <button class="small danger" data-act="delete">Delete</button>
      </div>
      <div class="camera-meta">
        ${camera.webhook_url ? "webhook configured" : "no webhook"} ·
        ${
          camera.zone.length >= 3
            ? `zone · ${(ZoneGeometry.polygonArea(camera.zone) * 100).toFixed(0)}% of frame`
            : "whole frame"
        }
      </div>`;
    card.querySelector("h3").textContent = camera.name;

    const video = card.querySelector("video");
    const canvas = card.querySelector("canvas");
    const status = card.querySelector(".video-status");

    attachStream(video, status, camera.id);

    card.addEventListener("click", (event) => {
      const action = event.target.dataset.act;
      if (!action) return;
      if (action === "identify") identifyCamera(camera);
      if (action === "zone") openZoneDialog(camera);
      if (action === "webhook") editWebhook(camera);
      if (action === "edit") openCameraDialog(camera);
      if (action === "delete") deleteCamera(camera);
    });

    requestAnimationFrame(() => drawSavedZone(camera, canvas));
    grid.appendChild(card);
  }
}

function attachStream(video, status, cameraId) {
  const src = `/api/cameras/${cameraId}/stream.m3u8`;
  if (window.Hls && window.Hls.isSupported()) {
    const hls = new window.Hls({ liveSyncDurationCount: 3 });
    hls.loadSource(src);
    hls.attachMedia(video);
    hls.on(window.Hls.Events.ERROR, (_event, data) => {
      if (data.fatal) {
        status.textContent = "stream error — retrying";
        setTimeout(() => hls.loadSource(src), 4000);
      }
    });
    state.hls.push(hls);
  } else if (video.canPlayType("application/vnd.apple.mpegurl")) {
    video.src = src;
  } else {
    status.textContent = "HLS not supported";
  }
  video.play().catch(() => {});
}

/* ------------------------------ zone editor ------------------------------ */

const zoneEditor = {
  camera: null,
  points: [],
  tool: "polygon",
  snap: true,
  undoStack: [],
  redoStack: [],
  rectUndo: null,
  dirty: false,
  drawing: false,
  dragIndex: -1,
  hoverIndex: -1,
  rectStart: null,
  rectCurrent: null,
  pointer: null,
  lastClick: null,
  imageReady: false,
};

function zoneSnapshot() {
  return zoneEditor.points.map((point) => [point[0], point[1]]);
}

function zonePushUndo() {
  zoneEditor.undoStack.push(zoneSnapshot());
  if (zoneEditor.undoStack.length > 60) zoneEditor.undoStack.shift();
  zoneEditor.redoStack = [];
}

function zoneMarkDirty() {
  zoneEditor.dirty = true;
  updateZoneStatus();
}

function zonePoint(event) {
  const canvas = $("#zone-canvas");
  const rect = canvas.getBoundingClientRect();
  let point = [
    (event.clientX - rect.left) / Math.max(1, rect.width),
    (event.clientY - rect.top) / Math.max(1, rect.height),
  ];
  point = [
    ZoneGeometry.clamp(point[0], 0, 1),
    ZoneGeometry.clamp(point[1], 0, 1),
  ];
  return zoneEditor.snap ? ZoneGeometry.snapPoint(point, 0.01) : point;
}

function zoneCanvas() {
  const canvas = $("#zone-canvas");
  const image = $("#zone-image");
  const width = Math.max(1, image.clientWidth || canvas.parentElement.clientWidth);
  const height = Math.max(1, image.clientHeight || Math.round(width * 0.5625));
  if (canvas.width !== width || canvas.height !== height) {
    canvas.width = width;
    canvas.height = height;
  }
  return { canvas, width, height };
}

function zonePixels(point, width, height) {
  return [point[0] * width, point[1] * height];
}

function zoneHitVertex(event) {
  const { width, height } = zoneCanvas();
  const point = zonePoint(event);
  const [px, py] = zonePixels(point, width, height);
  for (let index = 0; index < zoneEditor.points.length; index += 1) {
    const [vx, vy] = zonePixels(zoneEditor.points[index], width, height);
    if (Math.hypot(px - vx, py - vy) <= 12) return index;
  }
  return -1;
}

function zoneNearestEdge(event) {
  const { width, height } = zoneCanvas();
  const point = zonePoint(event);
  const [px, py] = zonePixels(point, width, height);
  let best = null;
  const points = zoneEditor.points;
  for (let index = 0; index < points.length; index += 1) {
    const [ax, ay] = zonePixels(points[index], width, height);
    const [bx, by] = zonePixels(points[(index + 1) % points.length], width, height);
    const hit = ZoneGeometry.distanceToSegment([px, py], [ax, ay], [bx, by]);
    if (hit.distance <= 10 && (best === null || hit.distance < best.distance)) {
      best = { index, distance: hit.distance, point: [hit.point[0] / width, hit.point[1] / height] };
    }
  }
  return best;
}

function zoneTrace(ctx, points, width, height, close) {
  ctx.beginPath();
  points.forEach(([x, y], index) => {
    const [px, py] = zonePixels([x, y], width, height);
    if (index === 0) ctx.moveTo(px, py);
    else ctx.lineTo(px, py);
  });
  if (close) ctx.closePath();
}

function drawZoneEditor() {
  const { canvas, width, height } = zoneCanvas();
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, width, height);
  if (!zoneEditor.imageReady) return;

  if (zoneEditor.snap) {
    ctx.strokeStyle = "rgba(255, 255, 255, 0.06)";
    ctx.lineWidth = 1;
    for (let step = 1; step < 10; step += 1) {
      const x = (width * step) / 10;
      const y = (height * step) / 10;
      ctx.beginPath();
      ctx.moveTo(x, 0);
      ctx.lineTo(x, height);
      ctx.stroke();
      ctx.beginPath();
      ctx.moveTo(0, y);
      ctx.lineTo(width, y);
      ctx.stroke();
    }
  }

  const points = zoneEditor.points;
  const closed = points.length >= 3;

  if (closed) {
    ctx.save();
    ctx.beginPath();
    ctx.rect(0, 0, width, height);
    zoneTrace(ctx, points, width, height, true);
    ctx.fillStyle = "rgba(0, 0, 0, 0.45)";
    ctx.fill("evenodd");
    ctx.restore();

    zoneTrace(ctx, points, width, height, true);
    ctx.fillStyle = "rgba(245, 166, 35, 0.16)";
    ctx.fill();
  }

  zoneTrace(ctx, points, width, height, closed);
  if (zoneEditor.drawing && zoneEditor.pointer && points.length > 0) {
    const [lx, ly] = zonePixels(points[points.length - 1], width, height);
    const [px, py] = zonePixels(zoneEditor.pointer, width, height);
    ctx.moveTo(lx, ly);
    ctx.lineTo(px, py);
  }
  ctx.strokeStyle = "#f5a623";
  ctx.lineWidth = 2.5;
  ctx.stroke();

  if (zoneEditor.rectStart && zoneEditor.rectCurrent) {
    const rect = ZoneGeometry.rectPoints(zoneEditor.rectStart, zoneEditor.rectCurrent);
    zoneTrace(ctx, rect, width, height, true);
    ctx.fillStyle = "rgba(62, 166, 255, 0.15)";
    ctx.fill();
    ctx.setLineDash([6, 5]);
    ctx.strokeStyle = "#3ea6ff";
    ctx.lineWidth = 2;
    ctx.stroke();
    ctx.setLineDash([]);
  }

  points.forEach((point, index) => {
    const [px, py] = zonePixels(point, width, height);
    const active = index === zoneEditor.dragIndex || index === zoneEditor.hoverIndex;
    ctx.beginPath();
    ctx.arc(px, py, active ? 7 : 5, 0, Math.PI * 2);
    ctx.fillStyle = active ? "#3ea6ff" : "#ffffff";
    ctx.fill();
    ctx.lineWidth = 2;
    ctx.strokeStyle = "#0e1116";
    ctx.stroke();
  });
}

function updateZoneStatus() {
  const points = zoneEditor.points;
  const area = ZoneGeometry.polygonArea(points);
  const info = $("#zone-info");
  info.textContent =
    points.length === 0
      ? "Whole frame"
      : `${points.length} point${points.length === 1 ? "" : "s"} · ${(area * 100).toFixed(1)}% of frame`;

  let warning = "";
  let blocking = false;
  if (points.length > 0 && points.length < 3) {
    warning = "Add at least 3 points, or use Whole frame.";
    blocking = true;
  } else if (ZoneGeometry.polygonSelfIntersects(points)) {
    warning = "Zone edges cross — move or remove a point.";
    blocking = true;
  } else if (points.length >= 3 && area < 0.002) {
    warning = "Very small area — detection may be unreliable.";
  }
  $("#zone-warning").textContent = warning;
  $("#zone-save").disabled = blocking;
}

function setZoneTool(tool) {
  zoneEditor.tool = tool;
  zoneEditor.rectStart = null;
  zoneEditor.rectCurrent = null;
  $$("[data-zone-tool]").forEach((button) =>
    button.classList.toggle("active", button.dataset.zoneTool === tool)
  );
  drawZoneEditor();
}

function loadZoneSnapshot(cameraId) {
  const image = $("#zone-image");
  const loading = $("#zone-loading");
  zoneEditor.imageReady = false;
  loading.textContent = "Loading snapshot…";
  loading.classList.remove("hidden");
  image.onload = () => {
    zoneEditor.imageReady = true;
    loading.classList.add("hidden");
    drawZoneEditor();
  };
  image.onerror = () => {
    loading.textContent = "No snapshot yet — check the stream, then refresh";
  };
  image.src = `/api/cameras/${cameraId}/snapshot.jpg?t=${Date.now()}`;
}

function openZoneDialog(camera) {
  zoneEditor.camera = camera;
  zoneEditor.points = camera.zone.map(([x, y]) => [x, y]);
  zoneEditor.undoStack = [];
  zoneEditor.redoStack = [];
  zoneEditor.rectUndo = null;
  zoneEditor.dirty = false;
  zoneEditor.drawing = false;
  zoneEditor.dragIndex = -1;
  zoneEditor.hoverIndex = -1;
  zoneEditor.rectStart = null;
  zoneEditor.rectCurrent = null;
  zoneEditor.pointer = null;
  zoneEditor.lastClick = null;
  zoneEditor.imageReady = false;
  $("#zone-camera-name").textContent = `— ${camera.name}`;
  $("#zone-snap").checked = true;
  zoneEditor.snap = true;
  setZoneTool("polygon");
  updateZoneStatus();
  $("#zone-dialog").showModal();
  loadZoneSnapshot(camera.id);
}

function closeZoneDialog(force = false) {
  if (!force && zoneEditor.dirty && !window.confirm("Discard unsaved zone changes?")) {
    return;
  }
  zoneEditor.camera = null;
  zoneEditor.dirty = false;
  $("#zone-dialog").close();
}

function undoZone() {
  if (zoneEditor.undoStack.length === 0) return;
  zoneEditor.redoStack.push(zoneSnapshot());
  zoneEditor.points = zoneEditor.undoStack.pop();
  zoneEditor.dragIndex = -1;
  zoneEditor.hoverIndex = -1;
  zoneEditor.drawing = zoneEditor.points.length > 0;
  zoneMarkDirty();
  drawZoneEditor();
}

function redoZone() {
  if (zoneEditor.redoStack.length === 0) return;
  zoneEditor.undoStack.push(zoneSnapshot());
  zoneEditor.points = zoneEditor.redoStack.pop();
  zoneEditor.dragIndex = -1;
  zoneEditor.hoverIndex = -1;
  zoneEditor.drawing = zoneEditor.points.length > 0;
  zoneMarkDirty();
  drawZoneEditor();
}

function clearZone() {
  if (zoneEditor.points.length === 0) return;
  zonePushUndo();
  zoneEditor.points = [];
  zoneEditor.drawing = false;
  zoneEditor.hoverIndex = -1;
  zoneMarkDirty();
  drawZoneEditor();
}

function removeZonePoint(index) {
  if (index < 0 || index >= zoneEditor.points.length) return;
  zonePushUndo();
  zoneEditor.points.splice(index, 1);
  zoneEditor.hoverIndex = -1;
  zoneEditor.dragIndex = -1;
  zoneMarkDirty();
  drawZoneEditor();
}

function zonePointerDown(event) {
  if (!zoneEditor.camera || event.button === 2) return;
  event.preventDefault();
  const canvas = $("#zone-canvas");
  canvas.setPointerCapture?.(event.pointerId);
  const point = zonePoint(event);
  zoneEditor.pointer = point;

  if (zoneEditor.tool === "rect") {
    zoneEditor.rectStart = point;
    zoneEditor.rectCurrent = point;
    zoneEditor.rectUndo = zoneSnapshot();
    drawZoneEditor();
    return;
  }

  const vertex = zoneHitVertex(event);
  if (vertex >= 0) {
    zonePushUndo();
    zoneEditor.dragIndex = vertex;
    zoneEditor.hoverIndex = vertex;
    zoneEditor.lastClick = null;
    drawZoneEditor();
    return;
  }

  const edge = zoneNearestEdge(event);
  if (edge && zoneEditor.points.length >= 3) {
    zonePushUndo();
    zoneEditor.points.splice(edge.index + 1, 0, edge.point);
    zoneEditor.dragIndex = edge.index + 1;
    zoneEditor.hoverIndex = edge.index + 1;
    zoneEditor.drawing = true;
    zoneMarkDirty();
    drawZoneEditor();
    return;
  }

  const now = performance.now();
  if (
    zoneEditor.lastClick &&
    now - zoneEditor.lastClick.ts < 350 &&
    Math.hypot(
      point[0] - zoneEditor.lastClick.point[0],
      point[1] - zoneEditor.lastClick.point[1]
    ) < 0.02
  ) {
    zoneEditor.lastClick = null;
    zoneEditor.drawing = false;
    drawZoneEditor();
    return;
  }
  if (zoneEditor.points.length >= 49) {
    toast("A zone can have at most 50 points", true);
    return;
  }
  zonePushUndo();
  zoneEditor.points.push(point);
  zoneEditor.dragIndex = zoneEditor.points.length - 1;
  zoneEditor.hoverIndex = zoneEditor.points.length - 1;
  zoneEditor.drawing = true;
  zoneEditor.lastClick = { ts: now, point };
  zoneMarkDirty();
  drawZoneEditor();
}

function zonePointerMove(event) {
  if (!zoneEditor.camera) return;
  const point = zonePoint(event);
  zoneEditor.pointer = point;
  const canvas = $("#zone-canvas");

  if (zoneEditor.dragIndex >= 0) {
    zoneEditor.points[zoneEditor.dragIndex] = point;
    zoneMarkDirty();
    drawZoneEditor();
    return;
  }
  if (zoneEditor.rectStart) {
    zoneEditor.rectCurrent = point;
    drawZoneEditor();
    return;
  }

  const vertex = zoneHitVertex(event);
  const edge = vertex < 0 ? zoneNearestEdge(event) : null;
  zoneEditor.hoverIndex = vertex;
  canvas.style.cursor = vertex >= 0 ? "grab" : edge ? "copy" : "crosshair";
  drawZoneEditor();
}

function zonePointerUp() {
  $("#zone-canvas").style.cursor = "crosshair";
  if (zoneEditor.rectStart) {
    const start = zoneEditor.rectStart;
    const end = zoneEditor.rectCurrent || start;
    if (
      Math.abs(end[0] - start[0]) >= 0.02 &&
      Math.abs(end[1] - start[1]) >= 0.02
    ) {
      zoneEditor.undoStack.push(zoneEditor.rectUndo || zoneSnapshot());
      zoneEditor.redoStack = [];
      zoneEditor.points = ZoneGeometry.rectPoints(start, end);
      zoneEditor.drawing = false;
      zoneMarkDirty();
    }
    zoneEditor.rectStart = null;
    zoneEditor.rectCurrent = null;
  }
  zoneEditor.dragIndex = -1;
  drawZoneEditor();
}

function zoneContextMenu(event) {
  if (!zoneEditor.camera) return;
  event.preventDefault();
  removeZonePoint(zoneHitVertex(event));
}

function zoneDoubleClick(event) {
  if (!zoneEditor.camera) return;
  if (zoneHitVertex(event) < 0) {
    zoneEditor.drawing = false;
    zoneEditor.lastClick = null;
    drawZoneEditor();
  }
}

function zoneKeydown(event) {
  if (!zoneEditor.camera) return;
  if (event.key === "Escape") {
    event.preventDefault();
    closeZoneDialog();
    return;
  }
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "z") {
    event.preventDefault();
    if (event.shiftKey) redoZone();
    else undoZone();
    return;
  }
  if (event.key === "Backspace" || event.key === "Delete") {
    if (zoneEditor.hoverIndex >= 0 && zoneEditor.dragIndex < 0) {
      event.preventDefault();
      removeZonePoint(zoneEditor.hoverIndex);
    }
    return;
  }
  if (event.key === "Enter" && event.target.tagName !== "BUTTON") {
    event.preventDefault();
    saveZoneEditor(event);
  }
}

async function saveZoneEditor(event) {
  event?.preventDefault?.();
  if (!zoneEditor.camera) return;
  const points = zoneEditor.points;
  if (points.length > 0 && points.length < 3) {
    $("#zone-warning").textContent = "Add at least 3 points, or use Whole frame.";
    return;
  }
  if (ZoneGeometry.polygonSelfIntersects(points)) {
    $("#zone-warning").textContent = "Zone edges cross — move or remove a point.";
    return;
  }
  const camera = zoneEditor.camera;
  const button = $("#zone-save");
  button.disabled = true;
  try {
    const result = await api(`/api/cameras/${camera.id}/zone`, {
      method: "PUT",
      body: { polygon: points.length >= 3 ? points : [] },
    });
    camera.zone = result.polygon;
    zoneEditor.dirty = false;
    $("#zone-dialog").close();
    toast(
      result.polygon.length >= 3
        ? "Detection area saved"
        : "Whole frame saved (zone cleared)"
    );
    loadCameras();
  } catch (err) {
    toast(err.message, true);
  } finally {
    button.disabled = false;
  }
}

function drawSavedZone(camera, canvas) {
  const rect = canvas.getBoundingClientRect();
  if (!rect.width || !rect.height) return;
  if (canvas.width !== Math.round(rect.width) || canvas.height !== Math.round(rect.height)) {
    canvas.width = Math.round(rect.width);
    canvas.height = Math.round(rect.height);
  }
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  if (camera.zone.length < 3) return;
  zoneTrace(ctx, camera.zone, canvas.width, canvas.height, true);
  ctx.fillStyle = "rgba(245, 166, 35, 0.10)";
  ctx.fill();
  ctx.strokeStyle = "#f5a623";
  ctx.lineWidth = 2;
  ctx.stroke();
}

function redrawZoneOverlays() {
  for (const camera of state.cameras) {
    const canvas = document.querySelector(
      `.video-wrap[data-id="${camera.id}"] canvas`
    );
    if (canvas) drawSavedZone(camera, canvas);
  }
}


async function editWebhook(camera) {
  const url = window.prompt("Webhook URL (empty to disable):", camera.webhook_url || "");
  if (url === null) return;
  const secret = window.prompt(
    "Webhook secret header value (optional):",
    ""
  );
  if (secret === null) return;
  try {
    await api(`/api/cameras/${camera.id}/webhook`, {
      method: "PUT",
      body: { url, secret },
    });
    toast("Webhook saved");
    loadCameras();
  } catch (err) {
    toast(err.message, true);
  }
}

async function deleteCamera(camera) {
  if (!window.confirm(`Delete camera "${camera.name}"?`)) return;
  try {
    await api(`/api/cameras/${camera.id}`, { method: "DELETE" });
    toast("Camera deleted");
    loadCameras();
  } catch (err) {
    toast(err.message, true);
  }
}

async function identifyCamera(camera) {
  toast("Identifying…");
  try {
    const result = await api(`/api/cameras/${camera.id}/identify`, {
      method: "POST",
    });
    showIdentify(result);
  } catch (err) {
    toast(err.message, true);
  }
}

function showIdentify(result) {
  const ok = result.match;
  const content = $("#identify-content");
  content.innerHTML = `
    <div class="result-match ${ok ? "ok" : "bad"}">${ok ? "MATCH" : "NO MATCH"}</div>
    <dl class="result-grid">
      <dt>Name</dt><dd>${result.name ? escapeHtml(result.name) : "—"}</dd>
      <dt>Confidence</dt><dd>${result.confidence} / 100</dd>
      <dt>Threshold</dt><dd>${result.threshold_used ?? "—"}</dd>
      <dt>Source</dt><dd>${result.source}</dd>
      <dt>Human detected</dt><dd>${result.human_detected ? "yes" : "no"}</dd>
      <dt>Similarity</dt><dd>${result.similarity ?? "—"}</dd>
    </dl>
    <div class="confidence-bar"><div style="width:${Math.min(100, result.confidence)}%"></div></div>
    ${result.face_crop_b64 ? `<img class="face-crop" alt="detected face" src="data:image/jpeg;base64,${result.face_crop_b64}">` : ""}`;
  $("#identify-dialog").showModal();
}

function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[char]));
}

/* --------------------------- camera dialog --------------------------- */

function openCameraDialog(camera = null) {
  $("#camera-dialog-title").textContent = camera ? "Edit camera" : "Add camera";
  $("#camera-id").value = camera ? camera.id : "";
  $("#camera-name").value = camera ? camera.name : "";
  $("#camera-mode").value = camera ? camera.mode : "python";
  $("#camera-host").value = camera ? camera.host : "";
  $("#camera-port").value = camera ? camera.port : 80;
  $("#camera-onvif-user").value = camera ? camera.onvif_username : "";
  $("#camera-onvif-pass").value = "";
  $("#camera-onvif-pass").placeholder = camera && camera.has_onvif_password ? "unchanged" : "";
  $("#camera-rtsp").value = camera ? camera.rtsp_url : "";
  $("#camera-motion-threshold").value =
    camera && camera.motion_threshold !== null ? camera.motion_threshold : "";
  $("#camera-motion-cooldown").value =
    camera && camera.motion_cooldown !== null ? camera.motion_cooldown : "";
  $("#camera-error").textContent = "";
  $("#discover-results").innerHTML = "";
  $("#discover-status").textContent = "";
  $("#camera-dialog").showModal();
}

async function submitCamera(event) {
  event.preventDefault();
  const id = $("#camera-id").value;
  const body = {
    name: $("#camera-name").value.trim(),
    mode: $("#camera-mode").value,
    host: $("#camera-host").value.trim(),
    port: Number($("#camera-port").value) || 80,
    onvif_username: $("#camera-onvif-user").value,
  };
  const password = $("#camera-onvif-pass").value;
  if (password) body.onvif_password = password;
  const rtsp = $("#camera-rtsp").value.trim();
  if (rtsp) body.rtsp_url = rtsp;
  const motionThreshold = $("#camera-motion-threshold").value;
  if (motionThreshold !== "") body.motion_threshold = Number(motionThreshold);
  else if (id) body.motion_threshold = null;
  const motionCooldown = $("#camera-motion-cooldown").value;
  if (motionCooldown !== "") body.motion_cooldown = Number(motionCooldown);
  else if (id) body.motion_cooldown = null;
  try {
    if (id) {
      await api(`/api/cameras/${id}`, { method: "PUT", body });
    } else {
      await api("/api/cameras", { method: "POST", body });
    }
    $("#camera-dialog").close();
    toast("Camera saved");
    loadCameras();
  } catch (err) {
    $("#camera-error").textContent = err.message;
  }
}

async function testOnvif() {
  const body = {
    host: $("#camera-host").value.trim(),
    port: Number($("#camera-port").value) || 80,
    username: $("#camera-onvif-user").value,
    password: $("#camera-onvif-pass").value,
  };
  $("#camera-error").textContent = "Testing…";
  try {
    const result = await api("/api/cameras/test-onvif", {
      method: "POST",
      body,
    });
    $("#camera-rtsp").value = result.rtsp_url;
    $("#camera-error").textContent = "ONVIF OK — RTSP URL filled in";
  } catch (err) {
    $("#camera-error").textContent = err.message;
  }
}

async function discoverCameras() {
  const status = $("#discover-status");
  const list = $("#discover-results");
  list.innerHTML = "";
  status.textContent = "Probing network…";
  try {
    const devices = await api("/api/cameras/discover?timeout=4");
    status.textContent = devices.length ? "" : "No cameras found";
    for (const device of devices) {
      const item = document.createElement("li");
      const label = device.name || device.hardware || "ONVIF device";
      item.textContent = `${device.host}:${device.onvif_port} — ${label}`;
      item.title = device.xaddr;
      item.addEventListener("click", () => {
        $("#camera-host").value = device.host;
        $("#camera-port").value = device.onvif_port;
        list.innerHTML = "";
        status.textContent = "Host filled from discovery";
      });
      list.appendChild(item);
    }
  } catch (err) {
    status.textContent = `Discovery failed: ${err.message}`;
  }
}

/* ------------------------------ persons ------------------------------ */

async function loadPersons() {
  try {
    state.persons = await api("/api/persons");
  } catch (err) {
    toast(err.message, true);
    return;
  }
  renderPersons();
}

function renderPersons() {
  const list = $("#person-list");
  list.innerHTML = "";
  $("#persons-empty").classList.toggle("hidden", state.persons.length > 0);
  for (const person of state.persons) {
    const card = document.createElement("div");
    card.className = "card person-card";
    card.innerHTML = `
      <div class="person-head">
        <strong></strong>
        <div class="row">
          <label class="pitch">threshold
            <input type="number" min="0" max="100" step="1" value="${person.threshold}">
          </label>
          <button class="small" data-act="threshold">Save</button>
          <button class="small danger" data-act="delete">Delete</button>
        </div>
      </div>
      <div class="thumbs"></div>
      <div class="row">
        <input type="file" accept="image/*" multiple class="hidden">
        <button class="small" data-act="add-photos">Add photos</button>
        <span class="pitch">${person.photo_ids.length} photo(s)</span>
      </div>`;
    card.querySelector("strong").textContent = person.name;
    const thumbs = card.querySelector(".thumbs");
    for (const photoId of person.photo_ids) {
      const thumb = document.createElement("div");
      thumb.className = "thumb";
      thumb.innerHTML = `<img loading="lazy" alt="${escapeHtml(person.name)}" src="/api/persons/${person.id}/photos/${photoId}"><button title="remove">×</button>`;
      thumb.querySelector("button").addEventListener("click", () =>
        deletePhoto(person, photoId)
      );
      thumbs.appendChild(thumb);
    }
    card.querySelector('[data-act="add-photos"]').addEventListener("click", () => {
      card.querySelector('input[type="file"]').click();
    });
    card.querySelector('input[type="file"]').addEventListener("change", (event) => {
      if (event.target.files.length) uploadPhotos(person, event.target.files);
    });
    card.querySelector('[data-act="threshold"]').addEventListener("click", () =>
      saveThreshold(person, card.querySelector('input[type="number"]').value)
    );
    card.querySelector('[data-act="delete"]').addEventListener("click", () =>
      deletePerson(person)
    );
    list.appendChild(card);
  }
}

async function addPerson(event) {
  event.preventDefault();
  const name = $("#person-name").value.trim();
  const threshold = Number($("#person-threshold").value);
  const files = $("#person-files").files;
  if (!name) return;
  try {
    const person = await api("/api/persons", {
      method: "POST",
      body: { name, threshold },
    });
    if (files.length) await uploadPhotos(person, files, true);
    $("#person-form").reset();
    $("#person-threshold").value = 95;
    toast("Person added");
    loadPersons();
  } catch (err) {
    toast(err.message, true);
  }
}

async function uploadPhotos(person, files, silent = false) {
  const form = new FormData();
  for (const file of files) form.append("files", file);
  try {
    await api(`/api/persons/${person.id}/photos`, {
      method: "POST",
      body: form,
    });
    if (!silent) toast("Photos added");
    loadPersons();
  } catch (err) {
    toast(err.message, true);
  }
}

async function deletePhoto(person, photoId) {
  if (!window.confirm("Remove this photo?")) return;
  try {
    await api(`/api/persons/${person.id}/photos/${photoId}`, {
      method: "DELETE",
    });
    loadPersons();
  } catch (err) {
    toast(err.message, true);
  }
}

async function deletePerson(person) {
  if (!window.confirm(`Delete ${person.name} and all photos?`)) return;
  try {
    await api(`/api/persons/${person.id}`, { method: "DELETE" });
    toast("Person deleted");
    loadPersons();
  } catch (err) {
    toast(err.message, true);
  }
}

async function saveThreshold(person, value) {
  try {
    await api(`/api/persons/${person.id}`, {
      method: "PUT",
      body: { threshold: Number(value) },
    });
    toast("Threshold saved");
    loadPersons();
  } catch (err) {
    toast(err.message, true);
  }
}

/* ------------------------------- events ------------------------------ */

async function loadEvents() {
  if (!state.cameras.length) {
    try {
      state.cameras = await api("/api/cameras");
    } catch {}
  }
  try {
    const events = await api("/api/events?limit=50");
    const rows = events.map((event) => {
      const camera = state.cameras.find((item) => item.id === event.camera_id);
      const webhook = event.detail.webhook || {};
      const confidence =
        event.detail.confidence !== undefined && event.detail.confidence !== null
          ? event.detail.confidence
          : "—";
      return `<tr>
        <td>${new Date(event.created_at).toLocaleString()}</td>
        <td>${escapeHtml(camera ? camera.name : event.camera_id.slice(0, 8))}</td>
        <td class="kind-${event.kind}">${event.kind}</td>
        <td>${confidence}</td>
        <td>${webhook.message ? escapeHtml(webhook.message) : "—"}</td>
      </tr>`;
    });
    $("#events-body").innerHTML =
      rows.join("") || '<tr><td colspan="5" class="muted">no events yet</td></tr>';
  } catch (err) {
    toast(err.message, true);
  }
}

/* ------------------------------ settings ----------------------------- */

async function loadSettings() {
  try {
    state.settings = await api("/api/settings");
  } catch (err) {
    toast(err.message, true);
    return;
  }
  const select = $("#llm-provider");
  select.innerHTML = state.settings.providers
    .map((item) => `<option value="${item.value}">${item.label}</option>`)
    .join("");
  select.value = state.settings.provider;
  $("#llm-model").value = state.settings.model;
  $("#llm-base").value = state.settings.base_url;
  $("#llm-key").value = "";
  $("#llm-key").placeholder = state.settings.has_key ? "•••• saved (leave empty to keep)" : "not set";
}

async function saveSettings(event) {
  event.preventDefault();
  const body = {
    provider: $("#llm-provider").value,
    model: $("#llm-model").value.trim(),
    base_url: $("#llm-base").value.trim(),
  };
  const key = $("#llm-key").value;
  if (key) body.api_key = key;
  try {
    await api("/api/settings", { method: "PUT", body });
    toast("Settings saved");
    loadSettings();
  } catch (err) {
    toast(err.message, true);
  }
}

async function testLlm() {
  toast("Testing provider…");
  try {
    await api("/api/settings/test", { method: "POST" });
    toast("Provider responded correctly");
  } catch (err) {
    toast(err.message, true);
  }
}

/* ------------------------------- wiring ------------------------------ */

$("#login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("#login-error").textContent = "";
  try {
    await api("/api/login", {
      method: "POST",
      body: {
        username: $("#login-user").value,
        password: $("#login-pass").value,
      },
    });
    $("#login-pass").value = "";
    showApp();
    switchTab("cameras");
  } catch (err) {
    $("#login-error").textContent = err.message;
  }
});

$("#logout").addEventListener("click", async () => {
  try {
    await api("/api/logout", { method: "POST" });
  } catch {}
  showLogin();
});

$$("#tabs button").forEach((button) =>
  button.addEventListener("click", () => switchTab(button.dataset.tab))
);

$("#add-camera").addEventListener("click", () => openCameraDialog());
$("#camera-form").addEventListener("submit", submitCamera);
$("#camera-cancel").addEventListener("click", () => $("#camera-dialog").close());
$("#onvif-test").addEventListener("click", testOnvif);
$("#discover-cameras").addEventListener("click", discoverCameras);
$("#person-form").addEventListener("submit", addPerson);
$("#settings-form").addEventListener("submit", saveSettings);
$("#llm-test").addEventListener("click", testLlm);
$("#refresh-events").addEventListener("click", loadEvents);
$("#identify-close").addEventListener("click", () => $("#identify-dialog").close());

$("#zone-form").addEventListener("submit", saveZoneEditor);
$("#zone-cancel").addEventListener("click", () => closeZoneDialog());
$("#zone-undo").addEventListener("click", undoZone);
$("#zone-redo").addEventListener("click", redoZone);
$("#zone-clear").addEventListener("click", clearZone);
$("#zone-refresh").addEventListener("click", () => {
  if (zoneEditor.camera) loadZoneSnapshot(zoneEditor.camera.id);
});
$("#zone-snap").addEventListener("change", (event) => {
  zoneEditor.snap = event.target.checked;
  drawZoneEditor();
});
$$("[data-zone-tool]").forEach((button) =>
  button.addEventListener("click", () => setZoneTool(button.dataset.zoneTool))
);
$("#zone-dialog").addEventListener("keydown", zoneKeydown);
$("#zone-dialog").addEventListener("close", () => {
  zoneEditor.camera = null;
});
const zoneCanvasEl = $("#zone-canvas");
zoneCanvasEl.addEventListener("pointerdown", zonePointerDown);
zoneCanvasEl.addEventListener("pointermove", zonePointerMove);
zoneCanvasEl.addEventListener("pointerup", zonePointerUp);
zoneCanvasEl.addEventListener("pointercancel", zonePointerUp);
zoneCanvasEl.addEventListener("contextmenu", zoneContextMenu);
zoneCanvasEl.addEventListener("dblclick", zoneDoubleClick);
window.addEventListener("resize", redrawZoneOverlays);
$("#zone-image").addEventListener("load", redrawZoneOverlays);

boot();
