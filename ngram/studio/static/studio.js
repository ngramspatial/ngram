const state = {
  overview: null,
  lineage: [],
  currentView: "overview",
  currentDomain: "identity",
  currentFile: "",
  currentSetting: "preferences",
  simulation: null,
};

const domainDescriptions = {
  identity: "Self-conception and voice",
  autobiography: "Episodes, people, beliefs",
  soma: "Tonic state and markers",
  agency: "Skills, policies, goals",
  embodiment: "Body and motion profile",
  world: "Local places and anchors",
  lineage: "Continuity transitions",
};

const settingHelp = {
  preferences: "Curiosity, cognition, and drive preferences.",
  policies: "Presence and unprompted-action boundaries.",
  schedule: "Wake cadence and recurring intentions.",
  embodiment: "Rig, motion tendencies, and gesture vocabulary.",
};

const chartColors = ["#c9ff56", "#77e2a6", "#7bdce8", "#b8a5ff", "#f1bd68", "#ff8d87", "#91a7ff"];

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: {
      "Content-Type": "application/json",
      "X-Ngram-Studio": "1",
      ...(options.headers || {}),
    },
    ...options,
  });
  const contentType = response.headers.get("content-type") || "";
  const body = contentType.includes("application/json") ? await response.json() : null;
  if (!response.ok) throw new Error(body?.detail || `${response.status} ${response.statusText}`);
  return body;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function bytes(value) {
  let amount = Number(value || 0);
  const units = ["B", "KB", "MB", "GB"];
  let index = 0;
  while (amount >= 1024 && index < units.length - 1) { amount /= 1024; index += 1; }
  return `${amount >= 10 || index === 0 ? amount.toFixed(0) : amount.toFixed(1)} ${units[index]}`;
}

function dateLabel(value) {
  if (!value) return "No timestamp";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
}

function shortHash(value) {
  const text = String(value || "");
  return text.length > 24 ? `${text.slice(0, 16)}…${text.slice(-7)}` : text || "—";
}

let toastTimer;
function toast(message, error = false) {
  const el = $("#toast");
  el.textContent = message;
  el.className = `toast show${error ? " error" : ""}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.className = "toast"; }, 3300);
}

function setView(view) {
  state.currentView = view;
  $$(".view").forEach((el) => el.classList.toggle("active", el.id === `view-${view}`));
  $$(".nav-item").forEach((el) => el.classList.toggle("active", el.dataset.view === view));
  if (view === "lineage") loadLineage();
  if (view === "domains") loadDomain(state.currentDomain);
  if (view === "soma") requestAnimationFrame(() => drawChart(state.simulation));
}

function renderShell(data) {
  const manifest = data.manifest || {};
  const name = manifest.display_name || "Unnamed entity";
  $("#entity-name").textContent = name;
  $("#entity-monogram").textContent = name.trim().slice(0, 1).toUpperCase() || "N";
  $("#entity-id").textContent = manifest.entity_id || "No entity id";
  $("#container-path").textContent = data.root || "—";
  $("#overview-title").textContent = `${name}, inspectable.`;

  const chip = $("#integrity-chip");
  chip.className = `status-chip ${data.verification.ok ? "good" : "bad"}`;
  chip.innerHTML = `<span></span>${data.verification.ok ? "Integrity verified" : "Verification failed"}`;

  const lock = data.lock || {};
  $("#lock-label").textContent = lock.status === "studio" ? "Held by Studio" : lock.status === "held" ? `Held by ${lock.purpose}` : "Available";
  $("#lock-dot").className = `status-dot ${lock.status === "studio" ? "good" : lock.status === "held" ? "bad" : ""}`;

  $("#recovery-banner").classList.toggle("hidden", !data.dirty);
  $("#recovery-copy").textContent = data.read_only_reason || "Studio is read-only until recovery completes.";
  const notice = data.notice || (!data.writable && !data.dirty ? data.read_only_reason : "");
  $("#notice-banner").classList.toggle("hidden", !notice);
  $("#notice-banner").textContent = notice || "";
  $("#export-button").classList.toggle("disabled", !data.writable);
  $("#save-settings").disabled = !data.writable;
  $("#settings-json").disabled = !data.writable;
  $("#editor-status").textContent = data.writable ? "Canonical · writable" : "Read only";
}

function barMarkup(values, compact = false) {
  const entries = Object.entries(values || {});
  if (!entries.length) return `<p class="muted">No saved bars in this container.</p>`;
  return entries.map(([name, raw]) => {
    const value = Math.max(0, Math.min(100, Number(raw || 0)));
    return `<div class="${compact ? "glance-row" : "current-soma-row"}">
      <label>${escapeHtml(name)}</label><strong>${value.toFixed(0)}%</strong>
      <div class="glance-track"><span style="width:${value}%"></span></div>
    </div>`;
  }).join("");
}

function renderOverview(data) {
  const verification = data.verification;
  const root = verification.actual_root || verification.expected_root || "";
  $("#integrity-short").textContent = verification.ok ? "Verified" : "Attention needed";
  $("#integrity-root").textContent = root || "No integrity root";
  $("#files-checked").textContent = `${verification.files_checked} files`;
  $("#lineage-count").textContent = String(data.lineage_count || 0).padStart(2, "0");
  $("#last-transition").textContent = data.last_transition?.type?.replaceAll("_", " ") || "No transitions yet";
  const totalFiles = data.domains.reduce((sum, item) => sum + item.files, 0);
  const totalBytes = data.domains.reduce((sum, item) => sum + item.bytes, 0);
  $("#domain-bytes").textContent = bytes(totalBytes);
  $("#domain-file-count").textContent = `${totalFiles} files across 7 domains`;
  $("#domain-map").innerHTML = data.domains.map((domain, index) => `<div class="domain-row">
    <span class="domain-index">0${index + 1}</span>
    <div><strong>${escapeHtml(domain.name)}</strong><small>${escapeHtml(domainDescriptions[domain.name] || "Canonical state")}</small></div>
    <span>${domain.files} · ${bytes(domain.bytes)}</span>
  </div>`).join("");
  const somaValues = data.soma?.state?.values || {};
  $("#soma-glance-bars").innerHTML = barMarkup(somaValues, true);
  $("#soma-current-bars").innerHTML = barMarkup(somaValues, false);
  $("#soma-saved-at").textContent = data.soma?.state?.saved_at ? dateLabel(Number(data.soma.state.saved_at) * 1000) : "No saved timestamp";
  const event = data.last_transition || {};
  $("#event-type").textContent = String(event.type || "No transition").replaceAll("_", " ");
  $("#event-time").textContent = dateLabel(event.timestamp);
  $("#event-hash").textContent = event.entry_hash || "—";
}

function renderDomainTabs(data) {
  $("#domain-tabs").innerHTML = data.domains.map((domain) => `<button class="domain-tab ${state.currentDomain === domain.name ? "active" : ""}" data-domain="${domain.name}">
    <span>${escapeHtml(domain.name)}</span><small>${domain.files}</small>
  </button>`).join("");
  $$(".domain-tab").forEach((button) => button.addEventListener("click", () => loadDomain(button.dataset.domain)));
}

async function loadDomain(domain) {
  state.currentDomain = domain;
  $$(".domain-tab").forEach((button) => button.classList.toggle("active", button.dataset.domain === domain));
  try {
    const data = await api(`/api/domains/${encodeURIComponent(domain)}`);
    $("#file-list").innerHTML = data.files.length ? data.files.map((file) => `<button class="file-row" data-path="${escapeHtml(file.path)}">
      <span>${file.kind === "json" ? "{}" : "¶"}</span><span><strong>${escapeHtml(file.name)}</strong><small>${bytes(file.bytes)}</small></span>
    </button>`).join("") : `<p class="muted">This domain is empty.</p>`;
    $$(".file-row").forEach((button) => button.addEventListener("click", () => loadFile(button.dataset.path, button)));
  } catch (error) { toast(error.message, true); }
}

async function loadFile(path, button) {
  try {
    const data = await api(`/api/file?path=${encodeURIComponent(path)}`);
    $$(".file-row").forEach((el) => el.classList.toggle("active", el === button));
    $("#preview-name").textContent = data.path;
    $("#preview-size").textContent = bytes(data.bytes);
    $("#preview-content").textContent = data.inspectable ? data.content : "This file is binary or exceeds the 2 MB inspection limit.";
  } catch (error) { toast(error.message, true); }
}

async function loadLineage() {
  try {
    const data = await api("/api/lineage");
    state.lineage = data.entries || [];
    $("#lineage-list").innerHTML = state.lineage.length ? state.lineage.map((entry) => {
      const payload = entry.payload && typeof entry.payload === "object" ? Object.entries(entry.payload).slice(0, 5) : [];
      return `<article class="lineage-entry">
        <span class="lineage-sequence">#${String(entry.sequence ?? 0).padStart(3, "0")}</span>
        <div class="lineage-axis"><span></span></div>
        <div class="lineage-body"><header><h3>${escapeHtml(String(entry.type || "transition").replaceAll("_", " "))}</h3><time>${escapeHtml(dateLabel(entry.timestamp))}</time></header>
        <code>${escapeHtml(shortHash(entry.entry_hash))}</code>
        <div class="lineage-payload">${payload.map(([key, value]) => `<span class="payload-item">${escapeHtml(key)} · ${escapeHtml(typeof value === "object" ? JSON.stringify(value) : value)}</span>`).join("")}</div></div>
      </article>`;
    }).join("") : `<p class="muted">No lineage entries found.</p>`;
  } catch (error) { toast(error.message, true); }
}

function renderSettings(data) {
  const settings = data.settings || {};
  $("#settings-tabs").innerHTML = Object.keys(settingHelp).map((name, index) => `<button class="settings-tab ${state.currentSetting === name ? "active" : ""}" data-setting="${name}">
    <small>0${index + 1}</small><strong>${name}</strong><small>${escapeHtml(settingHelp[name])}</small>
  </button>`).join("");
  $$(".settings-tab").forEach((button) => button.addEventListener("click", () => selectSetting(button.dataset.setting)));
  state.settings = settings;
  selectSetting(state.currentSetting);
}

function selectSetting(name) {
  state.currentSetting = name;
  $$(".settings-tab").forEach((button) => button.classList.toggle("active", button.dataset.setting === name));
  $("#settings-title").textContent = name[0].toUpperCase() + name.slice(1);
  $("#settings-help").textContent = settingHelp[name];
  $("#settings-json").value = JSON.stringify(state.settings?.[name] || {}, null, 2);
}

async function saveSetting() {
  let value;
  try { value = JSON.parse($("#settings-json").value); }
  catch (error) { toast(`Invalid JSON: ${error.message}`, true); return; }
  if (!value || Array.isArray(value) || typeof value !== "object") { toast("Settings must be a JSON object.", true); return; }
  const button = $("#save-settings");
  button.disabled = true;
  button.textContent = "Saving…";
  try {
    await api(`/api/settings/${state.currentSetting}`, { method: "PUT", body: JSON.stringify(value) });
    state.settings[state.currentSetting] = value;
    toast("Canonical settings saved. Lineage advanced.");
    await refreshOverview();
    await loadLineage();
  } catch (error) { toast(error.message, true); }
  finally {
    button.disabled = !state.overview?.writable;
    button.textContent = "Save and advance lineage";
  }
}

async function runSimulation(hours) {
  $("#hours-label").textContent = Number(hours) < 1 ? Number(hours).toFixed(2) : Number(hours).toFixed(Number(hours) % 1 ? 1 : 0);
  try {
    state.simulation = await api(`/api/soma/simulate?hours=${encodeURIComponent(hours)}&steps=32`);
    $("#simulation-source").textContent = state.simulation.source.replaceAll("-", " ");
    $("#projection-deltas").innerHTML = Object.entries(state.simulation.changes || {}).map(([name, delta]) => `<span class="delta-pill">${escapeHtml(name)} <strong>${Number(delta) > 0 ? "+" : ""}${Number(delta).toFixed(1)}</strong></span>`).join("");
    drawChart(state.simulation);
  } catch (error) { toast(error.message, true); }
}

function drawChart(simulation) {
  const canvas = $("#soma-chart");
  if (!simulation || !canvas || !canvas.offsetWidth) return;
  const dpr = window.devicePixelRatio || 1;
  const width = canvas.offsetWidth;
  const height = canvas.offsetHeight;
  canvas.width = width * dpr;
  canvas.height = height * dpr;
  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, width, height);
  const pad = { left: 35, right: 15, top: 24, bottom: 28 };
  const plotW = width - pad.left - pad.right;
  const plotH = height - pad.top - pad.bottom;
  ctx.font = "9px ui-monospace, monospace";
  ctx.fillStyle = "#59615c";
  ctx.strokeStyle = "rgba(255,255,255,.07)";
  ctx.lineWidth = 1;
  [0, 25, 50, 75, 100].forEach((tick) => {
    const y = pad.top + plotH * (1 - tick / 100);
    ctx.beginPath(); ctx.moveTo(pad.left, y); ctx.lineTo(width - pad.right, y); ctx.stroke();
    ctx.fillText(String(tick), 5, y + 3);
  });
  const rows = simulation.timeline || [];
  if (!rows.length) return;
  const names = Object.keys(rows[0].values || {});
  names.forEach((name, index) => {
    ctx.strokeStyle = chartColors[index % chartColors.length];
    ctx.lineWidth = 1.7;
    ctx.beginPath();
    rows.forEach((row, pointIndex) => {
      const x = pad.left + plotW * (pointIndex / Math.max(1, rows.length - 1));
      const value = Number(row.values[name] || 0);
      const y = pad.top + plotH * (1 - value / 100);
      if (pointIndex === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    });
    ctx.stroke();
    ctx.fillStyle = chartColors[index % chartColors.length];
    ctx.fillText(name, pad.left + index * 74, 10);
  });
  ctx.fillStyle = "#59615c";
  ctx.fillText("now", pad.left, height - 7);
  const endLabel = simulation.hours >= 24 ? `${(simulation.hours / 24).toFixed(simulation.hours % 24 ? 1 : 0)}d` : `${simulation.hours}h`;
  ctx.fillText(endLabel, width - pad.right - 22, height - 7);
}

async function refreshOverview() {
  const data = await api("/api/overview");
  state.overview = data;
  renderShell(data);
  renderOverview(data);
  renderDomainTabs(data);
  renderSettings(data);
}

async function recover() {
  const button = $("#recover-button");
  button.disabled = true;
  button.textContent = "Recovering…";
  try {
    await api("/api/recover", { method: "POST" });
    toast("Interrupted cache recovered into canonical state.");
    await refreshOverview();
  } catch (error) { toast(error.message, true); }
  finally { button.disabled = false; button.textContent = "Recover canonical state"; }
}

async function boot() {
  try {
    await refreshOverview();
    await runSimulation(24);
    setTimeout(() => $("#loading").classList.add("done"), 180);
  } catch (error) {
    $("#loading p").textContent = error.message;
    toast(error.message, true);
  }
}

$$('.nav-item').forEach((button) => button.addEventListener("click", () => setView(button.dataset.view)));
$$('[data-jump]').forEach((button) => button.addEventListener("click", () => setView(button.dataset.jump)));
$("#verify-button").addEventListener("click", async () => {
  try { await refreshOverview(); toast(state.overview.verification.ok ? "Every canonical file verifies." : "Verification found changes.", !state.overview.verification.ok); }
  catch (error) { toast(error.message, true); }
});
$("#recover-button").addEventListener("click", recover);
$("#save-settings").addEventListener("click", saveSetting);
let simulationTimer;
$("#hours-input").addEventListener("input", (event) => {
  clearTimeout(simulationTimer);
  simulationTimer = setTimeout(() => runSimulation(event.target.value), 90);
});
window.addEventListener("resize", () => drawChart(state.simulation));

boot();
