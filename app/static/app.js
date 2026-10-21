"use strict";

const state = {
  cameras: [],
  persons: [],
  settings: null,
  hls: [],
  zones: {},
  drawing: {},
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
    state.zones[camera.id] = camera.zone.slice();
    state.drawing[camera.id] = false;

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
        <button class="small hidden" data-act="zone-save">Save zone</button>
        <button class="small hidden" data-act="zone-cancel">Cancel</button>
        <button class="small" data-act="webhook">Webhook</button>
        <button class="small" data-act="edit">Edit</button>
        <button class="small danger" data-act="delete">Delete</button>
      </div>
      <div class="camera-meta">
        ${camera.webhook_url ? "webhook configured" : "no webhook"} ·
        ${camera.zone.length >= 3 ? "zone marked" : "whole frame"}
      </div>`;
    card.querySelector("h3").textContent = camera.name;

    const wrap = card.querySelector(".video-wrap");
    const video = card.querySelector("video");
    const canvas = card.querySelector("canvas");
    const status = card.querySelector(".video-status");

    attachStream(video, status, camera.id);
    canvas.addEventListener("pointerdown", (event) =>
      addZonePoint(event, camera, canvas, card)
    );
    canvas.addEventListener("pointermove", () => drawZone(camera, canvas));
    canvas.addEventListener("dblclick", () => finishZone(camera, card));
    window.addEventListener("resize", () => drawZone(camera, canvas));

    card.addEventListener("click", (event) => {
      const action = event.target.dataset.act;
      if (!action) return;
      if (action === "identify") identifyCamera(camera);
      if (action === "zone") startZone(camera, card);
      if (action === "zone-save") saveZone(camera, card);
      if (action === "zone-cancel") {
        state.drawing[camera.id] = false;
        state.zones[camera.id] = camera.zone.slice();
        card.querySelector(".video-wrap").classList.remove("drawing");
        toggleZoneButtons(card, false);
        drawZone(camera, canvas);
      }
      if (action === "webhook") editWebhook(camera);
      if (action === "edit") openCameraDialog(camera);
      if (action === "delete") deleteCamera(camera);
    });

    requestAnimationFrame(() => drawZone(camera, canvas));
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

function canvasPoint(event, canvas) {
  const rect = canvas.getBoundingClientRect();
  return [
    Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width)),
    Math.min(1, Math.max(0, (event.clientY - rect.top) / rect.height)),
  ];
}

function drawZone(camera, canvas) {
  const rect = canvas.getBoundingClientRect();
  if (canvas.width !== Math.round(rect.width) || canvas.height !== Math.round(rect.height)) {
    canvas.width = Math.round(rect.width);
    canvas.height = Math.round(rect.height);
  }
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  const points = state.zones[camera.id] || [];
  if (points.length === 0) return;
  ctx.lineWidth = 3;
  ctx.strokeStyle = state.drawing[camera.id] ? "#f5a623" : "#3ea6ff";
  ctx.fillStyle = "rgba(62, 166, 255, 0.12)";
  ctx.beginPath();
  points.forEach(([x, y], index) => {
    const px = x * canvas.width;
    const py = y * canvas.height;
    if (index === 0) ctx.moveTo(px, py);
    else ctx.lineTo(px, py);
  });
  if (state.drawing[camera.id]) {
    ctx.stroke();
  } else {
    ctx.closePath();
    ctx.fill();
    ctx.stroke();
  }
  points.forEach(([x, y]) => {
    ctx.beginPath();
    ctx.arc(x * canvas.width, y * canvas.height, 4, 0, Math.PI * 2);
    ctx.fillStyle = "#ffffff";
    ctx.fill();
  });
}

function addZonePoint(event, camera, canvas, card) {
  if (!state.drawing[camera.id]) return;
  event.preventDefault();
  state.zones[camera.id].push(canvasPoint(event, canvas));
  drawZone(camera, canvas);
  toggleZoneButtons(card, true);
}

function finishZone(camera, card) {
  if (!state.drawing[camera.id]) return;
  state.drawing[camera.id] = false;
  card.querySelector(".video-wrap").classList.remove("drawing");
  const canvas = card.querySelector("canvas");
  state.zones[camera.id] = state.zones[camera.id].slice(0, 49);
  drawZone(camera, canvas);
}

function startZone(camera, card) {
  state.drawing[camera.id] = true;
  state.zones[camera.id] = camera.zone.slice();
  card.querySelector(".video-wrap").classList.add("drawing");
  toggleZoneButtons(card, true);
  drawZone(camera, card.querySelector("canvas"));
  toast("Click on the video to add zone points, then Save zone");
}

function toggleZoneButtons(card, editing) {
  card.querySelector('[data-act="zone"]').classList.toggle("hidden", editing);
  card.querySelector('[data-act="zone-save"]').classList.toggle("hidden", !editing);
  card.querySelector('[data-act="zone-cancel"]').classList.toggle("hidden", !editing);
}

async function saveZone(camera, card) {
  finishZone(camera, card);
  const polygon = state.zones[camera.id];
  if (polygon.length > 0 && polygon.length < 3) {
    toast("A zone needs at least 3 points (or none for the whole frame)", true);
    return;
  }
  try {
    const result = await api(`/api/cameras/${camera.id}/zone`, {
      method: "PUT",
      body: { polygon },
    });
    camera.zone = result.polygon;
    state.zones[camera.id] = result.polygon.slice();
    toggleZoneButtons(card, false);
    drawZone(camera, card.querySelector("canvas"));
    toast("Zone saved");
  } catch (err) {
    toast(err.message, true);
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

boot();
