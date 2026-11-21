"use strict";
const el = id => document.getElementById(id);
const time = new Intl.DateTimeFormat(undefined, {hour: "numeric", minute: "2-digit", second: "2-digit"});
const date = new Intl.DateTimeFormat(undefined, {dateStyle: "medium"});
const routes = {general: "Scene description", guide: "Device guidance", locate: "Button location"};
const metrics = {stt: "Transcription", vision: "Vision", locate: "Button location", tts: "Speech generation",
  ttfa: "First audio", total: "Response", total_stream: "Stream complete"};
const duration = ms => Number.isFinite(ms) ? (ms / 1000).toFixed(2) + " s" : "—";
const title = r => r.ok == null ? "Processing" : routes[r.route] || (r.ok ? "Phone request" : "Failed request");
let records = [], selected = null, pinned = null, revision = null, online = false, configured = false;
let timer, fetching = false, speech, audioUrl;
const rows = new Map();

function show(id, visible) { el(id).hidden = !visible; }
function set(id, value) {
  if (el(id).textContent !== value) el(id).textContent = value;
}
function stamp(record, full = false) {
  const instant = new Date(record.time);
  if (Number.isNaN(instant.getTime())) return "Time unavailable";
  return full ? date.format(instant) + " · " + time.format(instant) : time.format(instant);
}
function stopAnswer() {
  speech?.abort();
  speech = null;
  const audio = el("answer-audio");
  audio.pause();
  if (audio.hasAttribute("src")) { audio.removeAttribute("src"); audio.load(); }
  if (audioUrl) URL.revokeObjectURL(audioUrl);
  audioUrl = null;
  show("answer-audio", false);
  set("play-status", "");
}
function drawMap(locate) {
  const root = el("keymap");
  root.replaceChildren();
  show("map-panel", Boolean(locate?.rows?.length));
  if (!locate?.rows?.length) return;
  const table = document.createElement("table");
  table.setAttribute("aria-label", "Remote buttons from top to bottom and left to right");
  locate.rows.forEach((row, r) => {
    const tr = table.insertRow();
    row.forEach((key, c) => {
      const cell = tr.insertCell();
      cell.textContent = key || "Unmarked";
      if (r + 1 === locate.target_row && c + 1 === locate.target_col) {
        cell.className = "target";
        cell.append(" · target");
      }
      if (locate.finger_visible && r + 1 === locate.finger_row && c + 1 === locate.finger_col) cell.append(" · finger");
    });
  });
  root.append(table);
}
function measures(list, entries) {
  const children = [];
  for (const [label, value] of entries) {
    if (value == null || value === "") continue;
    const dt = document.createElement("dt"), dd = document.createElement("dd");
    dt.textContent = label;
    dd.textContent = value;
    children.push(dt, dd);
  }
  list.replaceChildren(...children);
}
function renderDetail(record) {
  const changed = record?.id !== selected?.id;
  if (changed) {
    stopAnswer();
    el("recording").pause();
    el("map-panel").open = false;
    el("timing-panel").open = false;
  }
  const updated = JSON.stringify(record) !== JSON.stringify(selected);
  selected = record;
  show("detail", !!record);
  show("empty", !record);
  show("outcome", !!record);
  show("latest", !!pinned);
  if (!record) {
    if (changed) {
      el("photo").removeAttribute("src");
      el("original").removeAttribute("href");
      el("recording").removeAttribute("src");
      el("recording").load();
      set("question", "");
      set("reply", "");
    }
    return;
  }
  const canRead = online && configured && typeof record.reply === "string" &&
    record.reply.length <= 750 && /[\u0d00-\u0d7f]/.test(record.reply);
  show("listen", canRead && !audioUrl);
  el("listen").disabled = !!speech;
  if (!updated) return;
  const base = "/api/debug/" + encodeURIComponent(record.id);
  if (changed) {
    el("photo").src = base + "/image";
    el("original").href = base + "/image";
    if (record.audio_file) el("recording").src = base + "/audio";
    else el("recording").removeAttribute("src");
  }
  show("question-section", Boolean(record.transcript || record.audio_file));
  show("recording", !!record.audio_file);
  set("question", record.transcript || "");
  set("size", record.image_size || "Uploaded photo");
  set("when", title(record) + " · " + stamp(record, true));
  set("reply", record.reply || (record.ok == null ? "Waiting for the reply…" : "No reply recorded."));
  set("outcome", record.ok == null ? "Processing" : record.ok ? "Completed" : "Failed");
  el("outcome").dataset.state = record.ok == null ? "pending" : record.ok ? "ok" : "error";
  show("request-error", !!record.error && record.error !== record.reply);
  set("request-error", record.error || "");
  const timing = el("timings");
  measures(timing, Object.entries(metrics)
    .filter(([key]) => Number.isFinite(record.timings_ms?.[key]))
    .map(([key, label]) => [label, duration(record.timings_ms[key])]));
  show("timing-panel", !!timing.children.length);
  measures(el("request-info"), [
    ["ID", record.id], ["Action", record.action], ["Route", record.route],
    ["HTTP response", record.http_status], ["Transcript language", record.stt_language],
    ["Photo size", record.image_bytes ? Math.ceil(record.image_bytes / 1024) + " KB" : null],
    ["Recording length", record.audio_seconds != null ? record.audio_seconds + " s" : null],
    ["Recording size", record.audio_bytes ? Math.ceil(record.audio_bytes / 1024) + " KB" : null]
  ]);
  drawMap(record.locate);
}
function renderList() {
  const query = el("search").value.trim().toLocaleLowerCase();
  const filtered = records.filter(r => [title(r), r.transcript, r.reply, r.id]
    .some(t => t?.toLocaleLowerCase().includes(query)));
  const ids = new Set(filtered.map(r => r.id));
  for (const [id, row] of rows) {
    if (!ids.has(id)) { row.remove(); rows.delete(id); }
  }
  const list = el("history");
  filtered.forEach((r, i) => {
    let row = rows.get(r.id);
    if (!row) {
      row = document.createElement("li");
      const button = document.createElement("button");
      button.type = "button";
      button.append(document.createElement("strong"), document.createElement("time"), document.createElement("span"));
      button.onclick = () => {
        pinned = r.id;
        renderDetail(records.find(item => item.id === pinned));
        renderList();
        if (window.matchMedia("(max-width: 650px)").matches) el("selected-title").focus();
      };
      row.append(button);
      rows.set(r.id, row);
    }
    if (list.children[i] !== row) list.insertBefore(row, list.children[i] || null);
    const button = row.firstChild;
    const values = [title(r), stamp(r), r.transcript || r.reply || "Photo received"];
    [...button.children].forEach((child, n) => {
      if (child.textContent !== values[n]) child.textContent = values[n];
    });
    button.setAttribute("aria-current", String(r.id === selected?.id));
  });
  show("history-empty", !filtered.length);
  set("history-empty", records.length ? "No requests match your search." : "No requests to display.");
}
function render(historyMessage = "") {
  set("count", String(records.length));
  set("latency", duration(records[0]?.timings_ms?.ttfa));
  if (!records.some(r => r.id === pinned)) pinned = null;
  renderDetail(records.find(r => r.id === pinned) || records[0] || null);
  show("pair-empty", !records.length && !historyMessage);
  set("empty-message", historyMessage || "Pair the Android app, start its service, and press Volume Up. Your first photo and reply will appear here.");
  renderList();
}

async function read(path) {
  const response = await fetch(path, {cache: "no-store", signal: AbortSignal.timeout(8000)});
  if (!response.ok) throw Object.assign(new Error(response.statusText), {status: response.status});
  return response.json();
}
async function refresh() {
  if (fetching) return;
  clearTimeout(timer);
  fetching = true;
  el("refresh").disabled = true;
  try {
    const [service, history] = await Promise.allSettled([read("/api/status"), read("/api/debug/items")]);
    online = service.status === "fulfilled";
    configured = online && service.value.ai_ready;
    revision = online ? service.value.revision : null;
    set("connection", online ? (service.value.phase || "Server online").replaceAll("_", " ") : "Server offline");
    el("connection").dataset.online = String(online);
    set("ai", online ? (configured ? "Key configured" : "Key missing") : "Unavailable");
    let warning = "";
    if (history.status === "fulfilled") records = history.value;
    else if (history.reason.status === 404) {
      records = [];
      warning = "Phone history is disabled. Set PHONE_DEBUG=1 and restart the server to save new requests.";
    } else warning = "Phone history could not be refreshed. Showing the last available data.";
    if (!online) warning = "The local server is unreachable. Start DrishtiGuide and refresh this page.";
    set("notice", warning);
    show("notice", !!warning);
    render(warning);
    if (history.status === "rejected") set("count", records.length ? String(records.length) : "—");
  } finally {
    fetching = false;
    el("refresh").disabled = false;
    if (!document.hidden) timer = setTimeout(refresh, 3000);
  }
}
function keepPlayingRequest() {
  if (!selected) return;
  pinned = selected.id;
  show("latest", true);
}
el("recording").addEventListener("play", keepPlayingRequest);
el("listen").onclick = async () => {
  keepPlayingRequest();
  const record = selected, controller = new AbortController();
  speech = controller;
  el("listen").disabled = true;
  set("play-status", "Preparing Malayalam audio…");
  try {
    const response = await fetch("/api/speech", {method: "POST", signal: controller.signal,
      headers: {"Content-Type": "application/json"}, body: JSON.stringify({text: record.reply, revision})});
    if (!response.ok) throw new Error("Could not prepare speech. Refresh and try again.");
    const blob = await response.blob();
    if (speech !== controller) return;
    audioUrl = URL.createObjectURL(blob);
    const audio = el("answer-audio");
    audio.src = audioUrl;
    show("answer-audio", true);
    show("listen", false);
    set("play-status", "");
    await audio.play();
  } catch (err) {
    if (speech === controller) set("play-status", audioUrl ? "Press Play to hear the reply." : err.message);
  } finally {
    if (speech === controller) { speech = null; el("listen").disabled = false; }
  }
};
el("latest").onclick = () => { pinned = null; render(); el("selected-title").focus(); };
el("search").oninput = renderList;
el("refresh").onclick = refresh;
document.addEventListener("visibilitychange", () => {
  clearTimeout(timer);
  if (!document.hidden) refresh();
});
refresh();
