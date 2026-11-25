"use strict";

import { BrowserMultiFormatReader } from "@zxing/browser";

const CONFIG_STORAGE_KEY = "eventtech.web.config.v1";
const SESSION_STORAGE_KEY = "eventtech.web.session.v1";
const PAGE_SIZE = 1000;
const ARTICLE_STATUSES = [
  "Einsatzbereit",
  "Defekt",
  "In Reparatur",
  "Wartung erforderlich",
  "Ausgemustert",
];

const elements = {};
let projectConfig = null;
let session = null;
let currentUser = null;
let records = [];
let recordsById = new Map();
let activeArticle = null;
let activeLocation = null;
let cameraStream = null;
let cameraDetector = null;
let zxingControls = null;
let cameraTimer = null;
let cameraBusy = false;
let scanAccepted = false;
let lastScrollY = 0;

function cacheElements() {
  [
    "boot-screen", "topbar", "connection-state", "gate", "app-shell", "logout-button",
    "config-form", "project-url", "publishable-key", "login-form", "email", "password",
    "login-button", "inventory", "metric-articles", "metric-locations", "metric-ready", "article-rows",
    "empty-state", "empty-title", "empty-copy", "empty-create-button", "search-input",
    "status-filter", "refresh-button", "new-article-button", "scan-open-button",
    "signed-in-email", "location-cards", "article-dialog", "article-dialog-title",
    "manage-locations-button", "location-dialog", "location-dialog-title", "location-form",
    "location-type", "location-existing", "location-name", "location-parent-field",
    "location-parent-label", "location-parent", "location-barcode-field", "location-barcode",
    "location-width-field", "location-width", "location-height-field", "location-height-label", "location-height",
    "location-description-field", "location-description", "location-save-button",
    "article-form", "article-box-select", "article-save-button", "dmx-details",
    "dmx-mode-list", "scanner-dialog", "scanner-video", "scanner-message",
    "manual-scan-form", "manual-barcode", "toast-region",
  ].forEach((id) => {
    elements[id] = document.getElementById(id);
  });
}

function readStorage(key) {
  try {
    const value = localStorage.getItem(key);
    return value ? JSON.parse(value) : null;
  } catch (error) {
    throw new Error(`Browser-Speicher konnte nicht gelesen werden: ${error.message}`);
  }
}

function writeStorage(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch (error) {
    throw new Error(`Browser-Speicher konnte nicht beschrieben werden: ${error.message}`);
  }
}

function clearSessionStorage() {
  try {
    localStorage.removeItem(SESSION_STORAGE_KEY);
  } catch (error) {
    throw new Error(`Anmeldedaten konnten nicht aus dem Browser-Speicher entfernt werden: ${error.message}`);
  }
  session = null;
  currentUser = null;
}

function setConnectionStatus(message, connected = false) {
  const state = elements["connection-state"];
  state.classList.toggle("is-connected", connected);
  state.innerHTML = `<i></i>${escapeHtml(message)}`;
}

function showToast(message, isError = false, duration = 5200) {
  const toast = document.createElement("div");
  toast.className = `toast${isError ? " is-error" : ""}`;
  toast.textContent = message;
  elements["toast-region"].append(toast);
  window.setTimeout(() => toast.remove(), duration);
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (character) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[character]);
}

function normalizeProjectUrl(value) {
  const parsed = new URL(value.trim());
  if (parsed.protocol !== "https:" && parsed.hostname !== "localhost") {
    throw new Error("Die Supabase-Projekt-URL muss HTTPS verwenden.");
  }
  return parsed.origin;
}

function headersFor(accessToken = null, extraHeaders = {}) {
  return {
    apikey: projectConfig.anonKey,
    ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
    ...extraHeaders,
  };
}

async function readResponse(response) {
  const text = await response.text();
  let payload = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = { message: text };
    }
  }
  if (!response.ok) {
    const message = payload?.msg || payload?.message || payload?.error_description || payload?.error;
    throw new Error(message ? String(message) : `Supabase antwortete mit HTTP ${response.status}.`);
  }
  return payload;
}

async function authRequest(path, body, accessToken = null) {
  if (!projectConfig) throw new Error("Bitte zuerst dein Supabase-Projekt verbinden.");
  const response = await fetch(`${projectConfig.url}/auth/v1/${path}`, {
    method: "POST",
    headers: headersFor(accessToken, { "Content-Type": "application/json" }),
    body: JSON.stringify(body),
  });
  return readResponse(response);
}

function storeSession(newSession) {
  const expiresIn = Number(newSession.expires_in || 3600);
  session = {
    access_token: newSession.access_token,
    refresh_token: newSession.refresh_token,
    expires_at: Date.now() + expiresIn * 1000,
  };
  writeStorage(SESSION_STORAGE_KEY, session);
}

async function refreshSession() {
  if (!session?.refresh_token) {
    clearSessionStorage();
    throw new Error("Deine Sitzung ist abgelaufen. Bitte melde dich erneut an.");
  }
  try {
    const refreshed = await authRequest("token?grant_type=refresh_token", {
      refresh_token: session.refresh_token,
    });
    storeSession(refreshed);
  } catch (error) {
    clearSessionStorage();
    throw new Error(`Sitzung konnte nicht erneuert werden. Bitte erneut anmelden. (${error.message})`);
  }
}

async function validAccessToken() {
  if (!session?.access_token) throw new Error("Du bist nicht angemeldet.");
  if (session.expires_at < Date.now() + 45000) await refreshSession();
  return session.access_token;
}

async function apiRequest(path, options = {}, retry = true) {
  const accessToken = await validAccessToken();
  const response = await fetch(`${projectConfig.url}${path}`, {
    ...options,
    headers: headersFor(accessToken, options.headers || {}),
  });
  if (response.status === 401 && retry && session?.refresh_token) {
    await refreshSession();
    return apiRequest(path, options, false);
  }
  return readResponse(response);
}

function saveConfig(url, anonKey) {
  projectConfig = { url: normalizeProjectUrl(url), anonKey: anonKey.trim() };
  if (projectConfig.anonKey.length < 20) {
    throw new Error("Der Publishable-/anon-Key sieht unvollständig aus.");
  }
  writeStorage(CONFIG_STORAGE_KEY, projectConfig);
  elements["project-url"].value = projectConfig.url;
  elements["publishable-key"].value = projectConfig.anonKey;
  elements["login-button"].disabled = false;
}

function setAuthenticatedView(isAuthenticated) {
  elements["gate"].hidden = isAuthenticated;
  elements["app-shell"].hidden = !isAuthenticated;
  elements["logout-button"].hidden = !isAuthenticated;
  if (!isAuthenticated) {
    setConnectionStatus(projectConfig ? "Projekt bereit · anmelden" : "Nicht verbunden");
    records = [];
    recordsById = new Map();
    return;
  }
  setConnectionStatus("Angemeldet · live", true);
  elements["signed-in-email"].textContent = currentUser?.email || "angemeldet";
}

async function loadCloudRecords() {
  const allRecords = [];
  for (let start = 0; ; start += PAGE_SIZE) {
    const batch = await apiRequest(
      `/rest/v1/sync_records?select=id,entity_type,data,revision,deleted&deleted=eq.false&order=id`,
      { headers: { Range: `${start}-${start + PAGE_SIZE - 1}` } },
    );
    if (!Array.isArray(batch)) throw new Error("Supabase hat ein unerwartetes Datenformat zurückgegeben.");
    allRecords.push(...batch);
    if (batch.length < PAGE_SIZE) break;
  }
  records = allRecords;
  recordsById = new Map(records.map((record) => [record.id, record]));
  renderInventory();
}

function typeRecords(entityType) {
  return records.filter((record) => record.entity_type === entityType);
}

function recordById(id, entityType = null) {
  const record = recordsById.get(id);
  return record && (!entityType || record.entity_type === entityType) ? record : null;
}

function getLocationData() {
  return {
    warehouses: typeRecords("warehouses"),
    racks: typeRecords("racks"),
    levels: typeRecords("levels"),
    boxes: typeRecords("boxes"),
  };
}

function locationLabel(boxId) {
  if (!boxId) return "Kein Lagerplatz";
  const box = recordById(boxId, "boxes");
  if (!box) return "Unbekannter Lagerplatz";
  const level = recordById(box.data.level_id, "levels");
  const rack = level ? recordById(level.data.rack_id, "racks") : null;
  const warehouse = rack ? recordById(rack.data.warehouse_id, "warehouses") : null;
  return [
    warehouse?.data.name,
    rack?.data.name,
    level?.data.name,
    box.data.name,
  ].filter(Boolean).join(" / ") || "Lagerplatz";
}

function shortMark(name) {
  const words = String(name || "ET").trim().split(/\s+/).filter(Boolean);
  return words.slice(0, 2).map((word) => word[0]).join("").toUpperCase();
}

function statusClass(status) {
  switch (status) {
    case "Defekt": return "status-defect";
    case "In Reparatur": return "status-repair";
    case "Wartung erforderlich": return "status-maintenance";
    case "Ausgemustert": return "status-retired";
    default: return "";
  }
}

function renderInventory() {
  const articles = typeRecords("articles").sort((left, right) =>
    String(left.data.name || "").localeCompare(String(right.data.name || ""), "de"),
  );
  const barcodes = typeRecords("barcodes");
  const barcodesByArticle = new Map();
  for (const barcode of barcodes) {
    const articleId = barcode.data.article_id;
    if (!articleId || !barcode.data.code) continue;
    if (!barcodesByArticle.has(articleId)) barcodesByArticle.set(articleId, []);
    barcodesByArticle.get(articleId).push(barcode.data.code);
  }
  const allLocations = getLocationData();
  const readyCount = articles.filter((record) => (record.data.status || "Einsatzbereit") === "Einsatzbereit").length;
  const search = elements["search-input"].value.trim().toLocaleLowerCase("de");
  const status = elements["status-filter"].value;
  const filtered = articles.filter((record) => {
    const item = record.data;
    const searchable = [
      item.name, item.manufacturer, item.model, item.category, ...articleBarcodeValues(record, barcodesByArticle),
      item.description, locationLabel(item.box_id),
    ].join(" ").toLocaleLowerCase("de");
    return (!search || searchable.includes(search)) &&
      (!status || (item.status || "Einsatzbereit") === status);
  });

  elements["metric-articles"].textContent = articles.length.toLocaleString("de");
  elements["metric-locations"].textContent = (
    allLocations.warehouses.length + allLocations.racks.length +
    allLocations.levels.length + allLocations.boxes.length
  ).toLocaleString("de");
  elements["metric-ready"].textContent = readyCount.toLocaleString("de");
  elements["article-rows"].innerHTML = filtered.map((record) =>
    renderArticleRow(record, barcodesByArticle),
  ).join("");
  elements["empty-state"].hidden = filtered.length !== 0;
  if (articles.length === 0) {
    elements["empty-title"].textContent = "Noch keine Artikel";
    elements["empty-copy"].textContent = "Lege deinen ersten Artikel an, damit er im Browser verfügbar ist.";
  } else {
    elements["empty-title"].textContent = "Keine Treffer";
    elements["empty-copy"].textContent = "Passe Suche oder Statusfilter an.";
  }
  elements["location-cards"].innerHTML = renderLocationCards(allLocations);
  renderBoxOptions();
}

function articleBarcodeValues(record, barcodesByArticle) {
  return [...new Set([
    record.data.barcode,
    ...(barcodesByArticle.get(record.id) || []),
  ].filter(Boolean))];
}

function renderArticleRow(record, barcodeRecords) {
  const article = record.data;
  const status = article.status || "Einsatzbereit";
  const subtitle = [article.manufacturer, article.model, article.category].filter(Boolean).join(" · ");
  const barcodes = articleBarcodeValues(record, barcodeRecords);
  return `<tr>
    <td><div class="article-cell"><span class="article-mark">${escapeHtml(shortMark(article.name))}</span><span><strong class="article-name">${escapeHtml(article.name || "Unbenannter Artikel")}</strong><small class="article-subtitle">${escapeHtml(subtitle || "Keine Zusatzangaben")}</small></span></div></td>
    <td class="barcode-column"><span class="barcode-text">${escapeHtml(barcodes.join(" · ") || "—")}</span></td>
    <td><span class="location-text">${escapeHtml(locationLabel(article.box_id))}</span></td>
    <td><span class="status-pill ${statusClass(status)}">${escapeHtml(status)}</span></td>
    <td class="quantity-column"><span class="quantity">${escapeHtml(article.quantity ?? 0)}</span></td>
    <td><button class="row-action" type="button" data-edit-article="${escapeHtml(record.id)}" aria-label="Details und Bearbeitung für ${escapeHtml(article.name)} öffnen">↗</button></td>
  </tr>`;
}

function renderLocationCards(locations) {
  if (!locations.warehouses.length) {
    return `<p class="location-empty">Noch keine Lager angelegt. Lagerorte können in der Desktop-App eingerichtet und anschließend synchronisiert werden.</p>`;
  }
  return locations.warehouses.map((warehouse) => {
    const racks = locations.racks.filter((rack) => rack.data.warehouse_id === warehouse.id);
    const levels = locations.levels.filter((level) =>
      racks.some((rack) => rack.id === level.data.rack_id),
    );
    const boxes = locations.boxes.filter((box) =>
      levels.some((level) => level.id === box.data.level_id),
    );
    const itemCount = typeRecords("articles").filter((article) =>
      boxes.some((box) => box.id === article.data.box_id),
    ).length;
    return `<article class="location-card">
      <div class="location-card-top"><h3>${escapeHtml(warehouse.data.name || "Lager")}</h3><span class="location-kind">LAGER</span></div>
      <div class="location-count"><strong>${racks.length}</strong> Regale · <strong>${boxes.length}</strong> Boxen · <strong>${itemCount}</strong> Artikel</div>
    </article>`;
  }).join("");
}

function locationDisplayName(record, entityType) {
  if (entityType === "warehouses") return record.data.name || "Lager";
  if (entityType === "racks") {
    const warehouse = recordById(record.data.warehouse_id, "warehouses");
    return [warehouse?.data.name, record.data.name].filter(Boolean).join(" / ");
  }
  if (entityType === "levels") {
    const rack = recordById(record.data.rack_id, "racks");
    const warehouse = rack ? recordById(rack.data.warehouse_id, "warehouses") : null;
    return [warehouse?.data.name, rack?.data.name, record.data.name].filter(Boolean).join(" / ");
  }
  return locationLabel(record.id);
}

function renderLocationOptions() {
  const type = elements["location-type"].value;
  const currentRecords = typeRecords(type).slice().sort((left, right) =>
    locationDisplayName(left, type).localeCompare(locationDisplayName(right, type), "de"),
  );
  elements["location-existing"].innerHTML = [
    '<option value="">Neuen Lagerort anlegen</option>',
    ...currentRecords.map((record) =>
      `<option value="${escapeHtml(record.id)}">${escapeHtml(locationDisplayName(record, type))}</option>`,
    ),
  ].join("");
}

function renderLocationParent(type, selectedId = "") {
  const parentConfig = {
    racks: { type: "warehouses", label: "In welchem Lager steht das Regal?" },
    levels: { type: "racks", label: "Zu welchem Regal gehört der Boden?" },
    boxes: { type: "levels", label: "Auf welchem Boden steht die Box?" },
  }[type];
  const field = elements["location-parent-field"];
  field.hidden = !parentConfig;
  elements["location-parent"].required = Boolean(parentConfig);
  if (!parentConfig) {
    elements["location-parent"].innerHTML = "";
    return;
  }

  elements["location-parent-label"].textContent = parentConfig.label;
  const parents = typeRecords(parentConfig.type).slice().sort((left, right) =>
    locationDisplayName(left, parentConfig.type).localeCompare(
      locationDisplayName(right, parentConfig.type), "de",
    ),
  );
  elements["location-parent"].innerHTML = [
    `<option value="">${parents.length ? "Übergeordneten Lagerort auswählen" : "Erst den übergeordneten Lagerort anlegen"}</option>`,
    ...parents.map((record) =>
      `<option value="${escapeHtml(record.id)}">${escapeHtml(locationDisplayName(record, parentConfig.type))}</option>`,
    ),
  ].join("");
  elements["location-parent"].value = selectedId || "";
}

function updateLocationForm(record = null) {
  const type = elements["location-type"].value;
  activeLocation = record;
  renderLocationOptions();
  elements["location-existing"].value = record?.id || "";
  renderLocationParent(type, record?.data?.warehouse_id || record?.data?.rack_id || record?.data?.level_id || "");
  elements["location-width-field"].hidden = type !== "racks";
  elements["location-height-field"].hidden = !["racks", "levels"].includes(type);
  elements["location-description-field"].hidden = !["warehouses", "boxes"].includes(type);
  elements["location-barcode-field"].hidden = false;
  elements["location-width"].required = type === "racks";
  elements["location-height"].required = ["racks", "levels"].includes(type);
  elements["location-height-label"].textContent = type === "levels" ? "Bodenhöhe (cm)" : "Höhe (cm)";

  const data = record?.data || {};
  elements["location-name"].value = data.name || "";
  elements["location-barcode"].value = data.barcode || "";
  elements["location-width"].value = data.width_cm ?? (type === "racks" ? 120 : "");
  elements["location-height"].value = data.height_cm ?? (type === "racks" ? 200 : type === "levels" ? 40 : "");
  elements["location-description"].value = data.description || "";
  elements["location-dialog-title"].innerHTML = record
    ? 'Lagerort bearbeiten<span class="heading-period">.</span>'
    : 'Lagerort anlegen<span class="heading-period">.</span>';
}

function openLocationDialog(type = "warehouses") {
  elements["location-form"].reset();
  elements["location-type"].value = type;
  updateLocationForm();
  elements["location-save-button"].disabled = false;
  elements["location-dialog"].showModal();
  elements["location-name"].focus();
}

async function saveLocation(event) {
  event.preventDefault();
  if (!elements["location-form"].reportValidity()) return;
  const saveButton = elements["location-save-button"];
  saveButton.disabled = true;
  const wasEdit = Boolean(activeLocation);
  try {
    const type = elements["location-type"].value;
    const name = elements["location-name"].value.trim();
    const barcode = elements["location-barcode"].value.trim();
    if (!name) throw new Error("Bitte gib einen Namen ein.");
    const parentId = elements["location-parent"].value || null;
    const parentKey = { racks: "warehouse_id", levels: "rack_id", boxes: "level_id" }[type];
    if (parentKey && !parentId) throw new Error("Bitte wähle zuerst den übergeordneten Lagerort aus.");

    const data = { name, barcode: barcode || null };
    if (type === "warehouses" || type === "boxes") {
      data.description = elements["location-description"].value.trim();
    } else if (type === "racks") {
      data.width_cm = Number(elements["location-width"].value);
      data.height_cm = Number(elements["location-height"].value);
      if (!Number.isInteger(data.width_cm) || !Number.isInteger(data.height_cm)) {
        throw new Error("Breite und Höhe müssen ganze Zahlen in Zentimetern sein.");
      }
    } else if (type === "levels") {
      data.height_cm = Number(elements["location-height"].value);
      if (!Number.isInteger(data.height_cm)) throw new Error("Die Bodenhöhe muss eine ganze Zahl in Zentimetern sein.");
    }
    if (parentKey) data[parentKey] = parentId;

    const record = activeLocation;
    const change = {
      id: record?.id || crypto.randomUUID(),
      entity_type: type,
      data: { ...(record?.data || {}), ...data },
      deleted: false,
      expected_revision: record?.revision ?? null,
    };
    const result = await apiRequest("/rest/v1/rpc/eventtech_web_apply_sync", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ p_changes: [change] }),
    });
    if (result?.conflicts?.length) {
      throw new Error("Der Lagerort wurde zwischenzeitlich geändert. Lade die Daten neu und versuche es erneut.");
    }
  } catch (error) {
    showToast(`Lagerort konnte nicht gespeichert werden: ${error.message}`, true);
    saveButton.disabled = false;
    return;
  }
  elements["location-dialog"].close();
  showToast(wasEdit ? "Lagerort aktualisiert." : "Lagerort angelegt.");
  await loadInventory();
}

function renderBoxOptions(selectedId = "") {
  const locations = getLocationData();
  const boxes = locations.boxes.slice().sort((left, right) =>
    locationLabel(left.id).localeCompare(locationLabel(right.id), "de"),
  );
  elements["article-box-select"].innerHTML = [
    '<option value="">Kein Lagerplatz</option>',
    ...boxes.map((box) => `<option value="${escapeHtml(box.id)}">${escapeHtml(locationLabel(box.id))}</option>`),
  ].join("");
  elements["article-box-select"].value = selectedId || "";
}

function renderDmx(articleId) {
  const modes = typeRecords("dmx_modes").filter((mode) => mode.data.article_id === articleId);
  elements["dmx-details"].hidden = modes.length === 0;
  elements["dmx-mode-list"].innerHTML = modes.map((mode) => {
    const channels = typeRecords("dmx_channels")
      .filter((channel) => channel.data.mode_id === mode.id)
      .sort((left, right) => Number(left.data.channel_number) - Number(right.data.channel_number));
    const channelMarkup = channels.length
      ? `<div class="dmx-channel-list">${channels.map((channel) => {
        const values = `${channel.data.value_min ?? 0}–${channel.data.value_max ?? 255}`;
        return `<span class="dmx-channel">CH ${escapeHtml(channel.data.channel_number)} · ${escapeHtml(channel.data.function_name)} · ${escapeHtml(values)}</span>`;
      }).join("")}</div>`
      : '<p class="dmx-empty">Für diesen Modus sind noch keine Kanäle hinterlegt.</p>';
    return `<div class="dmx-mode"><div class="dmx-mode-title"><span>${escapeHtml(mode.data.name)}</span><span>${escapeHtml(mode.data.channel_count)} KANÄLE</span></div>${channelMarkup}</div>`;
  }).join("");
}

function openArticle(record = null) {
  activeArticle = record;
  const form = elements["article-form"];
  form.reset();
  elements["article-dialog-title"].innerHTML = record
    ? 'Artikel bearbeiten<span class="heading-period">.</span>'
    : 'Artikel anlegen<span class="heading-period">.</span>';
  const data = record?.data || {};
  const savedBarcodes = record ? articleBarcodeRecords(record.id) : [];
  form.elements.name.value = data.name || "";
  form.elements.manufacturer.value = data.manufacturer || "";
  form.elements.model.value = data.model || "";
  form.elements.category.value = data.category || "";
  form.elements.barcode.value = data.barcode || savedBarcodes[0]?.data.code || "";
  form.elements.quantity.value = data.quantity ?? 1;
  form.elements.status.value = data.status || "Einsatzbereit";
  form.elements.description.value = data.description || "";
  form.elements.technical_data.value = data.technical_data || "";
  renderBoxOptions(data.box_id || "");
  renderDmx(record?.id || "");
  elements["article-save-button"].disabled = false;
  elements["article-dialog"].showModal();
  form.elements.name.focus();
}

function validateArticleForm(data) {
  const name = data.name.trim();
  if (!name) throw new Error("Bitte gib einen Artikelnamen ein.");
  const quantity = Number(data.quantity);
  if (!Number.isSafeInteger(quantity) || quantity < 0) {
    throw new Error("Die Menge muss eine ganze Zahl ab 0 sein.");
  }
  if (!ARTICLE_STATUSES.includes(data.status)) throw new Error("Bitte wähle einen gültigen Artikelstatus.");
  const barcode = data.barcode.trim();
  if (barcode) {
    const duplicate = records.find((record) => {
      if (record.id === activeArticle?.id || record.deleted) return false;
      if (record.entity_type === "articles") return record.data.barcode === barcode;
      return record.entity_type === "barcodes" &&
        record.data.code === barcode &&
        record.data.article_id !== activeArticle?.id;
    });
    if (duplicate) throw new Error("Dieser Barcode ist bereits einem anderen Artikel zugeordnet.");
  }
  return { ...data, name, barcode, quantity };
}

function articleBarcodeRecords(articleId) {
  return typeRecords("barcodes").filter((record) =>
    record.data.article_id === articleId && record.data.entity_type === "article",
  );
}

function makeArticleChanges(formData, articleRecord) {
  const articleId = articleRecord?.id || crypto.randomUUID();
  const currentData = articleRecord?.data || {};
  const data = {
    ...currentData,
    name: formData.name,
    manufacturer: formData.manufacturer.trim(),
    model: formData.model.trim(),
    category: formData.category.trim(),
    barcode: formData.barcode || null,
    quantity: formData.quantity,
    box_id: formData.box_id || null,
    description: formData.description.trim(),
    technical_data: formData.technical_data.trim(),
    status: formData.status,
  };
  const changes = [{
    id: articleId,
    entity_type: "articles",
    data,
    deleted: false,
    expected_revision: articleRecord?.revision ?? null,
  }];
  const existingBarcodes = articleBarcodeRecords(articleId);
  const primaryBarcode = existingBarcodes[0] || null;

  if (formData.barcode) {
    const barcodeData = {
      ...(primaryBarcode?.data || {}),
      code: formData.barcode,
      code_type: primaryBarcode?.data.code_type || "Code 128",
      entity_type: "article",
      label: primaryBarcode?.data.label || null,
      scale: primaryBarcode?.data.scale || 100,
      article_id: articleId,
    };
    changes.push({
      id: primaryBarcode?.id || crypto.randomUUID(),
      entity_type: "barcodes",
      data: barcodeData,
      deleted: false,
      expected_revision: primaryBarcode?.revision ?? null,
    });
  } else if (primaryBarcode) {
    changes.push({
      id: primaryBarcode.id,
      entity_type: "barcodes",
      data: null,
      deleted: true,
      expected_revision: primaryBarcode.revision,
    });
  }
  return changes;
}

async function saveArticle(event) {
  event.preventDefault();
  if (!elements["article-form"].reportValidity()) return;
  const saveButton = elements["article-save-button"];
  saveButton.disabled = true;
  try {
    const raw = Object.fromEntries(new FormData(elements["article-form"]).entries());
    const formData = validateArticleForm(raw);
    const changes = makeArticleChanges(formData, activeArticle);
    const result = await apiRequest("/rest/v1/rpc/eventtech_web_apply_sync", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ p_changes: changes }),
    });
    if (result?.conflicts?.length) {
      throw new Error("Der Artikel wurde zwischenzeitlich auf einem anderen Gerät geändert. Lade den Bestand neu und versuche es erneut.");
    }
  } catch (error) {
    showToast(`Speichern fehlgeschlagen: ${error.message}`, true);
    saveButton.disabled = false;
    return;
  }
  const wasEdit = Boolean(activeArticle);
  elements["article-dialog"].close();
  showToast(wasEdit ? "Artikel erfolgreich aktualisiert." : "Artikel erfolgreich angelegt.");
  await loadInventory();
}

async function loadInventory(showConfirmation = false) {
  const button = elements["refresh-button"];
  button.disabled = true;
  button.classList.add("is-loading");
  try {
    await loadCloudRecords();
    if (showConfirmation) showToast("Bestand wurde aktualisiert.");
  } catch (error) {
    showToast(`Bestand konnte nicht geladen werden: ${error.message}`, true);
  } finally {
    button.disabled = false;
    button.classList.remove("is-loading");
  }
}

function showGate() {
  setAuthenticatedView(false);
  elements["password"].value = "";
  elements["login-button"].disabled = !projectConfig;
}

async function logIn(event) {
  event.preventDefault();
  if (!elements["login-form"].reportValidity()) return;
  try {
    const response = await authRequest("token?grant_type=password", {
      email: elements["email"].value.trim(),
      password: elements["password"].value,
    });
    storeSession(response);
    currentUser = response.user || null;
    setAuthenticatedView(true);
    await verifyCurrentUser();
    await loadCloudRecords();
    showToast("Du bist angemeldet.");
  } catch (error) {
    try {
      clearSessionStorage();
    } catch (storageError) {
      showToast(storageError.message, true);
    }
    showGate();
    showToast(`Anmeldung fehlgeschlagen: ${error.message}`, true);
  }
}

async function verifyCurrentUser() {
  currentUser = await apiRequest("/auth/v1/user");
  elements["signed-in-email"].textContent = currentUser.email || "angemeldet";
}

async function restoreSession() {
  try {
    projectConfig = readStorage(CONFIG_STORAGE_KEY);
    session = readStorage(SESSION_STORAGE_KEY);
  } catch (error) {
    showToast(error.message, true);
    return;
  }
  if (projectConfig?.url && projectConfig?.anonKey) {
    try {
      projectConfig.url = normalizeProjectUrl(projectConfig.url);
      elements["project-url"].value = projectConfig.url;
      elements["publishable-key"].value = projectConfig.anonKey;
      elements["login-button"].disabled = false;
    } catch (error) {
      showToast(`Projektkonfiguration ungültig: ${error.message}`, true);
      projectConfig = null;
    }
  }
  if (!session?.access_token || !projectConfig) return;
  try {
    await verifyCurrentUser();
    setAuthenticatedView(true);
    await loadCloudRecords();
  } catch (error) {
    try {
      clearSessionStorage();
    } catch (storageError) {
      showToast(storageError.message, true);
    }
    showGate();
    showToast(`Gespeicherte Anmeldung konnte nicht wiederhergestellt werden: ${error.message}`, true);
  }
}

async function logOut() {
  let logoutError = null;
  try {
    stopScanner();
    if (session?.access_token && projectConfig) {
      await apiRequest("/auth/v1/logout", { method: "POST" });
    }
  } catch (error) {
    logoutError = error;
  } finally {
    try {
      clearSessionStorage();
    } catch (error) {
      logoutError = logoutError || error;
    }
    showGate();
  }
  if (logoutError) showToast(`Lokal abgemeldet; Supabase konnte die Sitzung nicht bestätigen: ${logoutError.message}`, true);
  else showToast("Du wurdest abgemeldet.");
}

function openScanner() {
  elements["scanner-message"].textContent = "Kamera wird vorbereitet …";
  elements["manual-barcode"].value = "";
  scanAccepted = false;
  elements["scanner-dialog"].showModal();
  startScanner().catch((error) => {
    elements["scanner-message"].textContent = error.message;
  });
}

async function startScanner() {
  if (!navigator.mediaDevices?.getUserMedia) {
    throw new Error("Dein Browser stellt keinen Kamerazugriff bereit. Nutze einen USB-/Bluetooth-Scanner oder die manuelle Eingabe.");
  }
  elements["scanner-message"].textContent = "Barcode mittig in den Rahmen halten.";
  if (typeof window.BarcodeDetector === "function" &&
      typeof window.BarcodeDetector.getSupportedFormats === "function") {
    const supported = await window.BarcodeDetector.getSupportedFormats();
    const preferred = ["ean_13", "ean_8", "code_128", "upc_a", "upc_e", "qr_code"];
    const formats = preferred.filter((format) => supported.includes(format));
    if (formats.length) {
      cameraDetector = new window.BarcodeDetector({ formats });
      cameraStream = await navigator.mediaDevices.getUserMedia({
        audio: false,
        video: { facingMode: { ideal: "environment" } },
      });
      if (elements["scanner-dialog"].closed) {
        cameraStream.getTracks().forEach((track) => track.stop());
        cameraStream = null;
        return;
      }
      elements["scanner-video"].srcObject = cameraStream;
      await elements["scanner-video"].play();
      cameraTimer = window.setInterval(scanCameraFrame, 250);
      return;
    }
  }

  const reader = new BrowserMultiFormatReader();
  const controls = await reader.decodeFromVideoDevice(
    undefined,
    elements["scanner-video"],
    (result, error) => {
      if (result && !scanAccepted) {
        useScannedBarcode(result.getText());
      } else if (error && !["NotFoundException", "ChecksumException", "FormatException"].includes(error.name)) {
        elements["scanner-message"].textContent = `Scan fehlgeschlagen: ${error.message}`;
        stopScanner();
      }
    },
  );
  if (elements["scanner-dialog"].closed) controls.stop();
  else zxingControls = controls;
}

async function scanCameraFrame() {
  if (!cameraDetector || cameraBusy || elements["scanner-dialog"].closed) return;
  cameraBusy = true;
  try {
    const codes = await cameraDetector.detect(elements["scanner-video"]);
    const value = codes.find((code) => code.rawValue)?.rawValue;
    if (value) useScannedBarcode(value);
  } catch (error) {
    elements["scanner-message"].textContent = `Scan fehlgeschlagen: ${error.message}`;
    stopScanner();
  } finally {
    cameraBusy = false;
  }
}

function stopScanner() {
  if (cameraTimer) window.clearInterval(cameraTimer);
  cameraTimer = null;
  cameraDetector = null;
  if (zxingControls) zxingControls.stop();
  zxingControls = null;
  if (cameraStream) cameraStream.getTracks().forEach((track) => track.stop());
  cameraStream = null;
  elements["scanner-video"].srcObject = null;
}

function useScannedBarcode(value) {
  if (scanAccepted) return;
  scanAccepted = true;
  elements["search-input"].value = value.trim();
  renderInventory();
  stopScanner();
  elements["scanner-dialog"].close();
  const behavior = window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth";
  elements["inventory"].scrollIntoView({ behavior });
  showToast(`Suche nach Barcode ${value.trim()}`);
}

function bindEvents() {
  elements["config-form"].addEventListener("submit", (event) => {
    event.preventDefault();
    try {
      const previous = projectConfig;
      const nextUrl = normalizeProjectUrl(elements["project-url"].value);
      const nextKey = elements["publishable-key"].value.trim();
      if (previous && (previous.url !== nextUrl || previous.anonKey !== nextKey) && session) {
        clearSessionStorage();
        showGate();
      }
      saveConfig(nextUrl, nextKey);
      setConnectionStatus("Projekt bereit · anmelden");
      showToast("Supabase-Projekt wurde lokal in diesem Browser gespeichert.");
    } catch (error) {
      showToast(`Projekt konnte nicht gespeichert werden: ${error.message}`, true);
    }
  });
  elements["login-form"].addEventListener("submit", logIn);
  elements["logout-button"].addEventListener("click", logOut);
  elements["refresh-button"].addEventListener("click", () => loadInventory(true));
  elements["search-input"].addEventListener("input", renderInventory);
  elements["status-filter"].addEventListener("change", renderInventory);
  elements["new-article-button"].addEventListener("click", () => openArticle());
  elements["empty-create-button"].addEventListener("click", () => openArticle());
  elements["article-form"].addEventListener("submit", saveArticle);
  elements["manage-locations-button"].addEventListener("click", () => openLocationDialog());
  elements["location-form"].addEventListener("submit", saveLocation);
  elements["location-type"].addEventListener("change", () => updateLocationForm());
  elements["location-existing"].addEventListener("change", () => {
    const record = recordById(
      elements["location-existing"].value,
      elements["location-type"].value,
    );
    updateLocationForm(record);
  });
  elements["scan-open-button"].addEventListener("click", openScanner);
  elements["scanner-dialog"].addEventListener("close", stopScanner);
  elements["manual-scan-form"].addEventListener("submit", (event) => {
    event.preventDefault();
    const value = elements["manual-barcode"].value.trim();
    if (!value) {
      showToast("Bitte scanne oder gib zuerst einen Barcode ein.", true);
      return;
    }
    useScannedBarcode(value);
  });
  document.addEventListener("click", (event) => {
    const editButton = event.target.closest("[data-edit-article]");
    if (editButton) {
      const record = recordById(editButton.dataset.editArticle, "articles");
      if (record) openArticle(record);
    }
    const closeButton = event.target.closest("[data-close]");
    if (closeButton) document.getElementById(closeButton.dataset.close)?.close();
  });
  document.addEventListener("keydown", (event) => {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      if (!elements["app-shell"].hidden) elements["search-input"].focus();
    }
    if (event.key === "Escape") stopScanner();
  });
  window.addEventListener("scroll", () => {
    const currentY = window.scrollY;
    elements["topbar"].classList.toggle("is-hidden", currentY > lastScrollY && currentY > 160);
    lastScrollY = currentY;
  }, { passive: true });
}

async function initialize() {
  cacheElements();
  bindEvents();
  const keyboard = document.querySelector("kbd");
  if (keyboard) keyboard.textContent = /Mac|iPhone|iPad/.test(navigator.platform) ? "⌘ K" : "Ctrl K";
  window.setTimeout(() => elements["boot-screen"].classList.add("is-ready"), 180);
  await restoreSession();
}

document.addEventListener("DOMContentLoaded", initialize);
