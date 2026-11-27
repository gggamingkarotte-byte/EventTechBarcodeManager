"use strict";

import { BrowserMultiFormatReader } from "@zxing/browser";
import bwipjs from "bwip-js";
import {
  DEFAULT_SUPABASE_CONFIG,
  decryptSupabaseConfig,
  encryptSupabaseConfig,
} from "./supabase-config.js";
import { hasAutoUsageGuide, updateAutoUsageGuide } from "./usage-guides.js";

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
let currentProfile = null;
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
let currentInventoryId = "";
let scannerPurpose = "search";
let generatedBarcode = null;
let barcodePdfItems = [];
let dmxDraftModes = [];
let supabaseConfigFileMode = "export";

function cacheElements() {
  [
    "boot-screen", "topbar", "connection-state", "gate", "app-shell", "logout-button",
    "config-form", "project-url", "publishable-key", "login-form", "email", "password",
    "login-button", "create-account-button", "inventory", "metric-articles", "metric-locations", "metric-ready", "article-rows",
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
    "manual-scan-form", "manual-barcode", "toast-region", "dashboard-scans",
    "dashboard-scans-empty", "dashboard-scan-button", "rack-manage-button",
    "rack-warehouse-select", "rack-select", "rack-visual", "inventory-session-select",
    "inventory-warehouse-select", "inventory-start-button", "inventory-scan-form",
    "inventory-barcode", "inventory-rows", "inventory-camera-button", "inventory-finish-button",
    "inventory-export-button", "repair-add-button", "repair-rows", "barcode-generator-form",
    "barcode-type", "barcode-label", "barcode-custom-value", "barcode-article", "barcode-copies",
    "barcode-preview", "barcode-value", "barcode-save-button", "barcode-download-button",
    "barcode-add-pdf-button", "barcode-pdf-button", "barcode-pdf-clear-button",
    "barcode-rows", "barcode-count", "barcode-pdf-rows", "scan-search", "scan-rows",
    "scan-export-button", "camera-device-select", "scanner-prefix", "scanner-suffix",
    "camera-refresh-button", "backup-export-button", "backup-import-button",
    "backup-file-input", "settings-account", "settings-logout-button",
    "change-password-form", "current-password", "new-password", "confirm-password",
    "admin-approval-card", "refresh-pending-users", "pending-users-list",
    "supabase-config-dialog", "supabase-config-description", "supabase-config-password",
    "supabase-config-confirm-wrap", "supabase-config-confirm", "supabase-config-action",
    "supabase-config-file-input",
    "dmx-add-mode-button", "article-attachments", "article-files-input", "article-files-list",
    "article-files-note",
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

async function authRequest(path, body, accessToken = null, method = "POST") {
  if (!projectConfig) throw new Error("Bitte zuerst dein Supabase-Projekt verbinden.");
  const response = await fetch(`${projectConfig.url}/auth/v1/${path}`, {
    method,
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
  const nextConfig = { url: normalizeProjectUrl(url), anonKey: anonKey.trim() };
  if (nextConfig.anonKey.length < 20) {
    throw new Error("Der Publishable-/anon-Key sieht unvollständig aus.");
  }
  writeStorage(CONFIG_STORAGE_KEY, nextConfig);
  projectConfig = nextConfig;
  elements["project-url"].value = nextConfig.url;
  elements["publishable-key"].value = nextConfig.anonKey;
  elements["login-button"].disabled = false;
}

function openSupabaseConfigDialog(mode) {
  supabaseConfigFileMode = mode;
  elements["supabase-config-password"].value = "";
  elements["supabase-config-confirm"].value = "";
  elements["supabase-config-confirm-wrap"].hidden = mode !== "export";
  elements["supabase-config-description"].textContent = mode === "export"
    ? "Die Datei wird mit AES-GCM und deinem Passwort verschlüsselt. Verwende mindestens 12 Zeichen und bewahre das Passwort getrennt auf."
    : "Wähle deine verschlüsselte Verbindungsdatei aus und gib das zugehörige Passwort ein.";
  elements["supabase-config-action"].textContent = mode === "export"
    ? "Verschlüsselt exportieren"
    : "Datei auswählen";
  elements["supabase-config-dialog"].showModal();
}

async function exportSupabaseConfigFile() {
  const password = elements["supabase-config-password"].value;
  if (password.length < 12) throw new Error("Das Dateipasswort muss mindestens 12 Zeichen lang sein.");
  if (password !== elements["supabase-config-confirm"].value) {
    throw new Error("Die Dateipasswörter stimmen nicht überein.");
  }
  const settings = {
    project_url: projectConfig.url,
    public_key: projectConfig.anonKey,
  };
  if (settings.public_key.length < 20) {
    throw new Error("Der Publishable-/anon-Key sieht unvollständig aus.");
  }
  const encrypted = await encryptSupabaseConfig(settings, password);
  downloadBlob(
    new Blob([JSON.stringify(encrypted, null, 2)], { type: "application/json" }),
    "eventtech-supabase-settings.json",
  );
  showToast("Supabase-Verbindung wurde verschlüsselt exportiert.");
}

async function importSupabaseConfigFile(file) {
  if (!file || file.size > 64 * 1024) {
    throw new Error("Die Konfigurationsdatei fehlt oder ist zu groß.");
  }
  const password = elements["supabase-config-password"].value;
  let settings = await decryptSupabaseConfig(await file.text(), password);
  const nextUrl = normalizeProjectUrl(settings.project_url);
  const nextKey = settings.public_key;
  const containsSharedKey = Boolean(settings.shared_key);
  settings.shared_key = "";
  const changed = projectConfig && (projectConfig.url !== nextUrl || projectConfig.anonKey !== nextKey);
  if (changed && session) {
    clearSessionStorage();
    showGate();
  }
  saveConfig(nextUrl, nextKey);
  setConnectionStatus("Projekt bereit · anmelden");
  showToast(
    containsSharedKey
      ? "Projektverbindung importiert. Der Desktop-Organisationsschlüssel wird im Browser nicht gespeichert."
      : "Supabase-Projektverbindung wurde importiert.",
  );
  elements["supabase-config-dialog"].close();
}

function setAuthenticatedView(isAuthenticated) {
  elements["gate"].hidden = isAuthenticated;
  elements["app-shell"].hidden = !isAuthenticated;
  elements["logout-button"].hidden = !isAuthenticated;
  if (!isAuthenticated) {
    setConnectionStatus(projectConfig ? "Projekt bereit · anmelden" : "Nicht verbunden");
    records = [];
    recordsById = new Map();
    currentProfile = null;
    elements["admin-approval-card"].hidden = true;
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
  renderWorkspace();
}

function typeRecords(entityType) {
  return records.filter((record) => record.entity_type === entityType);
}

function recordById(id, entityType = null) {
  const record = recordsById.get(id);
  return record && (!entityType || record.entity_type === entityType) ? record : null;
}

async function applySyncChanges(changes, reload = true) {
  for (let start = 0; start < changes.length; start += 200) {
    const batch = changes.slice(start, start + 200);
    const result = await apiRequest("/rest/v1/rpc/eventtech_web_apply_sync", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ p_changes: batch }),
    });
    if (result?.conflicts?.length) {
      throw new Error("Ein oder mehrere Datensätze wurden gleichzeitig geändert. Bitte aktualisieren und erneut versuchen.");
    }
  }
  if (reload) await loadCloudRecords();
}

function syncChange(entityType, data, existing = null, id = crypto.randomUUID()) {
  return {
    id: existing?.id || id,
    entity_type: entityType,
    data,
    deleted: false,
    expected_revision: existing?.revision ?? null,
  };
}

function deleteSyncChange(record) {
  return {
    id: record.id,
    entity_type: record.entity_type,
    data: null,
    deleted: true,
    expected_revision: record.revision,
  };
}

function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function csvText(rows, fields) {
  const quote = (value) => `"${String(value ?? "").replace(/"/g, '""')}"`;
  return [
    fields.map(quote).join(";"),
    ...rows.map((row) => fields.map((field) => quote(row[field])).join(";")),
  ].join("\r\n");
}

function formatDate(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString("de-DE");
}

function recordArticleName(record) {
  return recordById(record?.data?.article_id, "articles")?.data.name || "Unbekannter Artikel";
}

function renderWorkspace() {
  renderDashboardScans();
  renderInventorySessions();
  renderInventoryRows();
  renderRepairs();
  renderBarcodeLibrary();
  renderBarcodePdfItems();
  renderScanHistory();
  renderRackGraphic();
  renderSettings();
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
    return `<p class="location-empty">Noch keine Lager angelegt. Lege sie über „Lagerort verwalten“ an.</p>`;
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

function renderDashboardScans() {
  const recent = typeRecords("scans")
    .slice()
    .sort((left, right) => String(right.data.timestamp || "").localeCompare(String(left.data.timestamp || "")))
    .slice(0, 8);
  elements["dashboard-scans"].innerHTML = recent.map((record) => {
    const article = recordById(record.data.article_id, "articles");
    return `<tr>
      <td>${escapeHtml(formatDate(record.data.timestamp))}</td>
      <td class="barcode-text">${escapeHtml(record.data.barcode || "—")}</td>
      <td>${escapeHtml(article?.data.name || "—")}</td>
      <td>${escapeHtml(locationLabel(article?.data.box_id || record.data.box_id))}</td>
      <td>${escapeHtml(record.data.scan_type || "—")}</td>
      <td>${escapeHtml(record.data.action || "—")}</td>
    </tr>`;
  }).join("");
  elements["dashboard-scans-empty"].hidden = recent.length > 0;
}

function renderInventorySessions() {
  const sessions = typeRecords("inventories")
    .slice()
    .sort((left, right) => String(right.data.started_at || "").localeCompare(String(left.data.started_at || "")));
  if (!sessions.some((record) => record.id === currentInventoryId)) {
    currentInventoryId = sessions.find((record) => !record.data.ended_at)?.id || "";
  }
  elements["inventory-session-select"].innerHTML = [
    '<option value="">Keine Inventur</option>',
    ...sessions.map((record) => {
      const warehouse = recordById(record.data.warehouse_id, "warehouses");
      const label = `${record.data.name || "Inventur"} · ${warehouse?.data.name || "Alle Lager"}${record.data.ended_at ? " · abgeschlossen" : " · aktiv"}`;
      return `<option value="${escapeHtml(record.id)}">${escapeHtml(label)}</option>`;
    }),
  ].join("");
  elements["inventory-session-select"].value = currentInventoryId;
  const warehouses = typeRecords("warehouses").slice().sort((a, b) =>
    String(a.data.name || "").localeCompare(String(b.data.name || ""), "de"),
  );
  elements["inventory-warehouse-select"].innerHTML = [
    '<option value="">Lager auswählen</option>',
    ...warehouses.map((record) =>
      `<option value="${escapeHtml(record.id)}">${escapeHtml(record.data.name || "Lager")}</option>`,
    ),
  ].join("");
  elements["inventory-start-button"].disabled = warehouses.length === 0;
  elements["inventory-finish-button"].disabled = !currentInventoryId ||
    Boolean(recordById(currentInventoryId, "inventories")?.data.ended_at);
}

function renderInventoryRows() {
  const inventory = recordById(currentInventoryId, "inventories");
  const items = inventory
    ? typeRecords("inventory_items")
      .filter((record) => record.data.inventory_id === inventory.id)
      .sort((left, right) => String(left.data.barcode || "").localeCompare(String(right.data.barcode || ""), "de"))
    : [];
  elements["inventory-rows"].innerHTML = items.map((record) => {
    const expected = Number(record.data.expected_quantity || 0);
    const scanned = Number(record.data.scanned_quantity || 0);
    const difference = scanned - expected;
    const differenceClass = difference < 0 ? "is-negative" : difference > 0 ? "is-positive" : "";
    return `<tr>
      <td class="barcode-text">${escapeHtml(record.data.barcode || "—")}</td>
      <td>${escapeHtml(recordArticleName(record))}</td>
      <td>${expected}</td><td>${scanned}</td>
      <td class="inventory-difference ${differenceClass}">${difference > 0 ? "+" : ""}${difference}</td>
    </tr>`;
  }).join("");
}

function renderRepairs() {
  const repairs = typeRecords("repairs")
    .slice()
    .sort((left, right) => String(right.data.date || "").localeCompare(String(left.data.date || "")));
  elements["repair-rows"].innerHTML = repairs.map((record) => {
    const status = record.data.repair_status || "Offen";
    return `<tr>
      <td>${escapeHtml(formatDate(record.data.date))}</td>
      <td>${escapeHtml(recordArticleName(record))}</td>
      <td>${escapeHtml(record.data.error_description || "—")}</td>
      <td>${escapeHtml(record.data.damage_type || "—")}</td>
      <td>${escapeHtml(record.data.measures || "—")}</td>
      <td>${escapeHtml(record.data.costs ?? "—")}</td>
      <td><select class="repair-status-select" data-repair-status="${escapeHtml(record.id)}">
        ${["Offen", "In Bearbeitung", "Abgeschlossen"].map((value) =>
          `<option${value === status ? " selected" : ""}>${value}</option>`).join("")}
      </select></td>
      <td>${escapeHtml(record.data.completion_date ? formatDate(record.data.completion_date) : "—")}</td>
    </tr>`;
  }).join("");
}

function renderBarcodeLibrary() {
  const barcodes = typeRecords("barcodes")
    .slice()
    .sort((left, right) => String(right.data.code || "").localeCompare(String(left.data.code || "")));
  elements["barcode-count"].textContent = `${barcodes.length} Einträge`;
  elements["barcode-rows"].innerHTML = barcodes.map((record) => {
    const label = record.data.label || recordArticleName(record);
    const assignment = record.data.article_id
      ? recordById(record.data.article_id, "articles")?.data.name || "Artikel"
      : record.data.entity_type === "custom" ? "Eigener Barcode" : "—";
    return `<tr>
      <td>${escapeHtml(label === record.data.code ? "—" : label)}</td>
      <td class="barcode-text">${escapeHtml(record.data.code || "—")}</td>
      <td>${escapeHtml(record.data.code_type || "Code 128")}</td>
      <td>${escapeHtml(assignment)}</td>
      <td><div class="table-actions">
        <button class="button button-quiet" data-barcode-add="${escapeHtml(record.id)}" type="button">Zum PDF</button>
        <button class="button button-quiet" data-barcode-edit="${escapeHtml(record.id)}" type="button">Bearbeiten</button>
        <button class="button button-quiet" data-barcode-delete="${escapeHtml(record.id)}" type="button">Löschen</button>
      </div></td>
    </tr>`;
  }).join("");
  elements["barcode-article"].innerHTML = [
    '<option value="">Nicht zugewiesen</option>',
    ...typeRecords("articles").slice().sort((a, b) =>
      String(a.data.name || "").localeCompare(String(b.data.name || ""), "de"),
    ).map((record) => `<option value="${escapeHtml(record.id)}">${escapeHtml(record.data.name || "Artikel")}</option>`),
  ].join("");
}

function renderBarcodePdfItems() {
  elements["barcode-pdf-rows"].innerHTML = barcodePdfItems.map((item, index) =>
    `<tr><td>${escapeHtml(item.label || "—")}</td><td class="barcode-text">${escapeHtml(item.code)}</td><td>${escapeHtml(item.codeType)}</td><td><button class="button button-quiet" data-pdf-remove="${index}" type="button">Entfernen</button></td></tr>`,
  ).join("");
}

function renderScanHistory() {
  const term = elements["scan-search"].value.trim().toLocaleLowerCase("de");
  const scans = typeRecords("scans")
    .filter((record) => {
      const article = recordById(record.data.article_id, "articles");
      const searchable = [
        record.data.barcode, article?.data.name, record.data.action,
        record.data.scan_type, locationLabel(article?.data.box_id || record.data.box_id),
      ].join(" ").toLocaleLowerCase("de");
      return !term || searchable.includes(term);
    })
    .sort((left, right) => String(right.data.timestamp || "").localeCompare(String(left.data.timestamp || "")));
  elements["scan-rows"].innerHTML = scans.map((record) => {
    const article = recordById(record.data.article_id, "articles");
    return `<tr>
      <td>${escapeHtml(formatDate(record.data.timestamp))}</td>
      <td class="barcode-text">${escapeHtml(record.data.barcode || "—")}</td>
      <td>${escapeHtml(article?.data.name || "—")}</td>
      <td>${escapeHtml(locationLabel(article?.data.box_id || record.data.box_id))}</td>
      <td>${escapeHtml(record.data.scan_type || "—")}</td>
      <td>${escapeHtml(record.data.action || "—")}</td>
    </tr>`;
  }).join("");
}

function renderRackGraphic() {
  const locations = getLocationData();
  const oldWarehouse = elements["rack-warehouse-select"].value;
  const warehouses = locations.warehouses.slice().sort((a, b) =>
    String(a.data.name || "").localeCompare(String(b.data.name || ""), "de"),
  );
  elements["rack-warehouse-select"].innerHTML = warehouses.map((record) =>
    `<option value="${escapeHtml(record.id)}">${escapeHtml(record.data.name || "Lager")}</option>`,
  ).join("");
  const warehouseId = warehouses.some((record) => record.id === oldWarehouse)
    ? oldWarehouse : warehouses[0]?.id || "";
  elements["rack-warehouse-select"].value = warehouseId;
  const racks = locations.racks.filter((record) => record.data.warehouse_id === warehouseId)
    .sort((a, b) => String(a.data.name || "").localeCompare(String(b.data.name || ""), "de"));
  const oldRack = elements["rack-select"].value;
  elements["rack-select"].innerHTML = racks.map((record) =>
    `<option value="${escapeHtml(record.id)}">${escapeHtml(record.data.name || "Regal")}</option>`,
  ).join("");
  const rackId = racks.some((record) => record.id === oldRack) ? oldRack : racks[0]?.id || "";
  elements["rack-select"].value = rackId;
  if (!rackId) {
    elements["rack-visual"].innerHTML = '<p class="location-empty">Lege zuerst Lager, Regale, Böden und Boxen an.</p>';
    return;
  }
  const levels = locations.levels.filter((record) => record.data.rack_id === rackId)
    .sort((a, b) => String(a.data.name || "").localeCompare(String(b.data.name || ""), "de"));
  elements["rack-visual"].innerHTML = levels.map((level) => {
    const boxes = locations.boxes.filter((record) => record.data.level_id === level.id);
    return `<article class="rack-level">
      <div class="rack-level-name"><strong>${escapeHtml(level.data.name || "Boden")}</strong><span>${escapeHtml(level.data.height_cm ?? 40)} cm</span></div>
      <div class="rack-boxes">${boxes.map((box) => {
        const articles = typeRecords("articles").filter((article) => article.data.box_id === box.id);
        return `<div class="rack-box" data-drop-box="${escapeHtml(box.id)}">
          <header><strong>${escapeHtml(box.data.name || "Box")}</strong><span>${articles.length} Artikel</span></header>
          ${articles.length ? articles.map((article) =>
            `<div class="rack-article" draggable="true" data-rack-article="${escapeHtml(article.id)}">${escapeHtml(article.data.name || "Artikel")}<span>${escapeHtml(article.data.quantity ?? 0)}×</span></div>`,
          ).join("") : '<span class="rack-empty">Leer · Artikel hier ablegen</span>'}
        </div>`;
      }).join("")}</div>
    </article>`;
  }).join("") || '<p class="location-empty">Für dieses Regal sind noch keine Böden angelegt.</p>';
}

function renderSettings() {
  elements["settings-account"].textContent = currentUser?.email || "Nicht angemeldet";
  elements["admin-approval-card"].hidden = currentProfile?.role !== "admin";
  const settings = readStorage("eventtech.web.settings.v1") || {};
  if (document.activeElement !== elements["scanner-prefix"]) {
    elements["scanner-prefix"].value = settings.prefix || "";
  }
  if (document.activeElement !== elements["scanner-suffix"]) {
    elements["scanner-suffix"].value = settings.suffix || "";
  }
  const selectedCamera = settings.cameraId || "";
  if ([...elements["camera-device-select"].options].some((option) => option.value === selectedCamera)) {
    elements["camera-device-select"].value = selectedCamera;
  }
}

function articleByBarcode(code) {
  const article = typeRecords("articles").find((record) => record.data.barcode === code);
  if (article) return article;
  const linkedBarcode = typeRecords("barcodes").find((record) =>
    record.data.code === code && record.data.article_id,
  );
  return linkedBarcode ? recordById(linkedBarcode.data.article_id, "articles") : null;
}

function barcodeExists(code) {
  return typeRecords("articles").some((record) => record.data.barcode === code) ||
    typeRecords("barcodes").some((record) => record.data.code === code) ||
    Object.values(getLocationData()).some((items) =>
      items.some((record) => record.data.barcode === code),
    );
}

function ean13CheckDigit(value) {
  const sum = [...value].reduce((total, digit, index) =>
    total + Number(digit) * (index % 2 ? 3 : 1), 0);
  return String((10 - (sum % 10)) % 10);
}

function generateUniqueBarcode(type) {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    let code;
    if (type === "EAN-13") {
      const body = `2${String(Math.floor(Math.random() * 1e11)).padStart(11, "0")}`;
      code = body + ean13CheckDigit(body);
    } else {
      code = `ET-${new Date().toISOString().slice(2, 7).replace("-", "")}-${crypto.randomUUID().slice(0, 6).toUpperCase()}`;
    }
    if (!barcodeExists(code)) return code;
  }
  throw new Error("Es konnte kein eindeutiger Barcode erstellt werden. Bitte erneut versuchen.");
}

function barcodeOptions(code, type) {
  const options = {
    text: code,
    scale: 3,
    includetext: false,
    padding: 8,
  };
  if (type === "EAN-13") {
    if (!/^\d{13}$/.test(code) || ean13CheckDigit(code.slice(0, 12)) !== code[12]) {
      throw new Error("Der EAN-13-Code ist ungültig.");
    }
    return { ...options, bcid: "ean13", text: code.slice(0, 12), height: 14 };
  }
  if (type === "QR") return { ...options, bcid: "qrcode", text: code, scale: 4 };
  if (type === "Code 128") return { ...options, bcid: "code128", height: 14 };
  throw new Error("Unbekannter Barcode-Typ.");
}

function makeBarcodeCanvas(code, type) {
  const canvas = document.createElement("canvas");
  bwipjs.toCanvas(canvas, barcodeOptions(code, type));
  return canvas;
}

function renderGeneratedBarcode() {
  if (!generatedBarcode) return;
  try {
    const canvas = makeBarcodeCanvas(generatedBarcode.code, generatedBarcode.codeType);
    elements["barcode-preview"].replaceChildren(canvas);
    elements["barcode-value"].value = generatedBarcode.code;
  } catch (error) {
    elements["barcode-preview"].textContent = error.message;
    showToast(`Barcode-Vorschau fehlgeschlagen: ${error.message}`, true);
  }
}

function addBarcodeToPdf(item, copies = 1) {
  for (let index = 0; index < copies; index += 1) {
    barcodePdfItems.push({ ...item });
  }
  renderBarcodePdfItems();
  showToast(`${copies} Etikett${copies === 1 ? "" : "en"} zum PDF hinzugefügt.`);
}

async function saveGeneratedBarcode() {
  if (!generatedBarcode) {
    showToast("Generiere zuerst einen Barcode.", true);
    return;
  }
  const articleId = elements["barcode-article"].value;
  const article = articleId ? recordById(articleId, "articles") : null;
  const existing = typeRecords("barcodes").find((record) => record.data.code === generatedBarcode.code);
  if (barcodeExists(generatedBarcode.code) && !existing) {
    showToast("Dieser Barcode ist bereits vergeben.", true);
    return;
  }
  const changes = [];
  if (article) {
    changes.push(syncChange("articles", { ...article.data, barcode: generatedBarcode.code }, article));
  }
  const barcodeData = {
    ...(existing?.data || {}),
    code: generatedBarcode.code,
    code_type: generatedBarcode.codeType,
    entity_type: article ? "article" : "custom",
    article_id: article?.id || null,
    label: elements["barcode-label"].value.trim() || article?.data.name || null,
    scale: 100,
  };
  changes.push(syncChange("barcodes", barcodeData, existing));
  elements["barcode-save-button"].disabled = true;
  try {
    await applySyncChanges(changes);
    showToast(article ? "Barcode gespeichert und dem Artikel zugewiesen." : "Barcode in der Liste gespeichert.");
  } catch (error) {
    showToast(`Barcode konnte nicht gespeichert werden: ${error.message}`, true);
  } finally {
    elements["barcode-save-button"].disabled = false;
  }
}

function editBarcodeRecord(recordId) {
  const record = recordById(recordId, "barcodes");
  if (!record) return;
  generatedBarcode = {
    code: record.data.code,
    codeType: record.data.code_type || "Code 128",
    label: record.data.label || "",
  };
  elements["barcode-type"].value = generatedBarcode.codeType;
  elements["barcode-label"].value = generatedBarcode.label;
  elements["barcode-custom-value"].value = generatedBarcode.code;
  elements["barcode-article"].value = record.data.article_id || "";
  renderGeneratedBarcode();
  elements["barcode-tools"].scrollIntoView({
    behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth",
  });
  showToast("Barcode geladen. Änderungen mit „In Liste speichern“ übernehmen.");
}

async function deleteBarcodeRecord(recordId) {
  const barcode = recordById(recordId, "barcodes");
  if (!barcode) throw new Error("Barcode wurde nicht gefunden.");
  if (!window.confirm(`Barcode „${barcode.data.code}“ wirklich aus der Liste löschen?`)) return;
  const changes = [syncChange("barcodes", null, barcode)];
  changes[0].deleted = true;
  const article = barcode.data.article_id
    ? recordById(barcode.data.article_id, "articles")
    : null;
  if (article?.data.barcode === barcode.data.code) {
    const replacement = articleBarcodeRecords(article.id).find((record) => record.id !== barcode.id);
    changes.push(syncChange("articles", {
      ...article.data,
      barcode: replacement?.data.code || null,
    }, article));
  }
  await applySyncChanges(changes);
  showToast("Barcode wurde gelöscht.");
}

async function downloadGeneratedBarcode() {
  if (!generatedBarcode) {
    showToast("Generiere zuerst einen Barcode.", true);
    return;
  }
  try {
    const canvas = makeBarcodeCanvas(generatedBarcode.code, generatedBarcode.codeType);
    const blob = await new Promise((resolve, reject) => canvas.toBlob((result) =>
      result ? resolve(result) : reject(new Error("PNG konnte nicht erstellt werden.")), "image/png"));
    downloadBlob(blob, `${generatedBarcode.code}.png`);
  } catch (error) {
    showToast(`PNG konnte nicht heruntergeladen werden: ${error.message}`, true);
  }
}

async function printBarcodePdf() {
  if (!barcodePdfItems.length) {
    showToast("Füge zuerst Barcodes zum PDF-Raster hinzu.", true);
    return;
  }
  const printWindow = window.open("", "_blank");
  if (!printWindow) {
    showToast("Der Browser hat das Druckfenster blockiert. Erlaube Pop-ups für diese Website.", true);
    return;
  }
  try {
    const labels = barcodePdfItems.map((item) => {
      const canvas = makeBarcodeCanvas(item.code, item.codeType);
      return `<article class="label"><strong>${escapeHtml(item.label || "")}</strong><img alt="" src="${canvas.toDataURL("image/png")}"><span>${escapeHtml(item.code)}</span></article>`;
    }).join("");
    printWindow.document.write(`<!doctype html><html lang="de"><head><meta charset="utf-8"><title>Barcode-Etiketten</title><style>
      @page{size:A4;margin:12mm}*{box-sizing:border-box}body{margin:0;font:10pt Arial,sans-serif;color:#111}
      .sheet{display:grid;grid-template-columns:repeat(2,1fr);grid-auto-rows:49mm;gap:2mm 8mm}
      .label{display:flex;flex-direction:column;align-items:center;justify-content:center;overflow:hidden;text-align:center;padding:2mm}
      .label strong{max-width:100%;margin-bottom:2mm;font-size:10pt;overflow-wrap:anywhere}
      .label img{max-width:65mm;max-height:18mm;object-fit:contain}.label span{margin-top:2mm;font:9pt Consolas,monospace}
      @media screen{body{padding:10mm;background:#eef0ec}.sheet{width:186mm;min-height:273mm;margin:auto;padding:0;background:#fff;box-shadow:0 4mm 18mm #0002}}
    </style></head><body><main class="sheet">${labels}</main><script>window.addEventListener("load",()=>setTimeout(()=>window.print(),250));</script></body></html>`);
    printWindow.document.close();
  } catch (error) {
    printWindow.close();
    showToast(`PDF-Druckansicht konnte nicht erstellt werden: ${error.message}`, true);
  }
}

function scannerValue(rawValue) {
  const settings = readStorage("eventtech.web.settings.v1") || {};
  let value = String(rawValue || "").trim();
  if (settings.prefix && value.startsWith(settings.prefix)) value = value.slice(settings.prefix.length);
  if (settings.suffix && value.endsWith(settings.suffix)) value = value.slice(0, -settings.suffix.length);
  return value.trim();
}

async function saveScan(code, scanType, action) {
  const article = articleByBarcode(code);
  const locations = getLocationData();
  const matchedLocation = Object.values(locations).flat().find((record) => record.data.barcode === code);
  const data = {
    timestamp: new Date().toISOString(),
    barcode: code,
    article_id: article?.id || null,
    box_id: matchedLocation?.entity_type === "boxes" ? matchedLocation.id : article?.data.box_id || null,
    scan_type: scanType,
    action: action || (article ? "Artikel erkannt" : matchedLocation ? "Lagerplatz erkannt" : "Unbekannt"),
  };
  await applySyncChanges([syncChange("scans", data)]);
}

function renderInventoryItemsCsv() {
  const inventory = recordById(currentInventoryId, "inventories");
  if (!inventory) {
    showToast("Wähle zuerst eine Inventur aus.", true);
    return;
  }
  const rows = typeRecords("inventory_items")
    .filter((record) => record.data.inventory_id === inventory.id)
    .map((record) => ({
      barcode: record.data.barcode,
      artikel: recordArticleName(record),
      soll: record.data.expected_quantity,
      ist: record.data.scanned_quantity,
      abweichung: Number(record.data.scanned_quantity || 0) - Number(record.data.expected_quantity || 0),
    }));
  downloadBlob(new Blob(["\ufeff", csvText(rows, ["barcode", "artikel", "soll", "ist", "abweichung"])], {
    type: "text/csv;charset=utf-8",
  }), "inventur.csv");
}

async function startInventory() {
  const warehouseId = elements["inventory-warehouse-select"].value;
  const warehouse = recordById(warehouseId, "warehouses");
  if (!warehouse) {
    showToast("Wähle zuerst ein Lager aus.", true);
    return;
  }
  const rackIds = typeRecords("racks")
    .filter((record) => record.data.warehouse_id === warehouse.id)
    .map((record) => record.id);
  const levelIds = typeRecords("levels")
    .filter((record) => rackIds.includes(record.data.rack_id))
    .map((record) => record.id);
  const boxIds = typeRecords("boxes")
    .filter((record) => levelIds.includes(record.data.level_id))
    .map((record) => record.id);
  const articles = typeRecords("articles").filter((record) => boxIds.includes(record.data.box_id));
  const startedAt = new Date().toISOString();
  const inventoryData = {
    name: `Inventur ${new Date().toLocaleString("de-DE", { dateStyle: "short", timeStyle: "short" })}`,
    warehouse_id: warehouse.id,
    started_at: startedAt,
    ended_at: null,
  };
  const inventoryChange = syncChange("inventories", inventoryData);
  currentInventoryId = inventoryChange.id;
  try {
    const items = articles.map((article) => {
      const code = article.data.barcode ||
        typeRecords("barcodes").find((record) => record.data.article_id === article.id)?.data.code || "";
      return syncChange("inventory_items", {
        inventory_id: inventoryChange.id,
        article_id: article.id,
        barcode: code,
        expected_quantity: Number(article.data.quantity || 0),
        scanned_quantity: 0,
      });
    });
    await applySyncChanges([inventoryChange, ...items]);
    showToast(`Inventur für ${warehouse.data.name} gestartet (${articles.length} Artikel im Sollbestand).`);
  } catch (error) {
    currentInventoryId = "";
    showToast(`Inventur konnte nicht gestartet werden: ${error.message}`, true);
  }
}

async function captureInventoryScan(event) {
  event?.preventDefault();
  const code = scannerValue(elements["inventory-barcode"].value);
  if (!code) {
    showToast("Scanne oder gib zuerst einen Barcode ein.", true);
    return;
  }
  const inventory = recordById(currentInventoryId, "inventories");
  if (!inventory || inventory.data.ended_at) {
    showToast("Wähle eine aktive Inventur aus oder starte eine neue.", true);
    return;
  }
  const article = articleByBarcode(code);
  const existingItem = typeRecords("inventory_items").find((record) =>
    record.data.inventory_id === inventory.id &&
    (article ? record.data.article_id === article.id : !record.data.article_id && record.data.barcode === code),
  );
  const changes = [];
  if (existingItem) {
    changes.push(syncChange("inventory_items", {
      ...existingItem.data,
      scanned_quantity: Number(existingItem.data.scanned_quantity || 0) + 1,
    }, existingItem));
  } else {
    changes.push(syncChange("inventory_items", {
      inventory_id: inventory.id,
      article_id: article?.id || null,
      barcode: code,
      expected_quantity: 0,
      scanned_quantity: 1,
    }));
  }
  changes.push(syncChange("scans", {
    timestamp: new Date().toISOString(),
    barcode: code,
    article_id: article?.id || null,
    box_id: article?.data.box_id || null,
    scan_type: "Inventur",
    action: article ? "Inventur erfasst" : "Unbekannter Barcode bei Inventur",
  }));
  elements["inventory-barcode"].value = "";
  try {
    await applySyncChanges(changes);
    elements["inventory-barcode"].focus();
    showToast(article ? `${article.data.name} bei der Inventur erfasst.` : "Unbekannter Barcode als Zusatz erfasst.");
  } catch (error) {
    showToast(`Scan konnte nicht gespeichert werden: ${error.message}`, true);
  }
}

async function finishInventory() {
  const inventory = recordById(currentInventoryId, "inventories");
  if (!inventory || inventory.data.ended_at) return;
  if (!window.confirm(`Inventur „${inventory.data.name}“ abschließen?`)) return;
  try {
    await applySyncChanges([syncChange("inventories", {
      ...inventory.data,
      ended_at: new Date().toISOString(),
    }, inventory)]);
    showToast("Inventur abgeschlossen.");
  } catch (error) {
    showToast(`Inventur konnte nicht abgeschlossen werden: ${error.message}`, true);
  }
}

async function updateRepairStatus(repairId, status) {
  const repair = recordById(repairId, "repairs");
  if (!repair) return;
  const completionDate = status === "Abgeschlossen"
    ? repair.data.completion_date || new Date().toISOString()
    : null;
  try {
    await applySyncChanges([syncChange("repairs", {
      ...repair.data,
      repair_status: status,
      completion_date: completionDate,
    }, repair)]);
  } catch (error) {
    showToast(`Reparaturstatus konnte nicht gespeichert werden: ${error.message}`, true);
    renderRepairs();
  }
}

function openRepairDialog() {
  const articleSelect = document.getElementById("repair-article");
  articleSelect.innerHTML = typeRecords("articles")
    .slice()
    .sort((a, b) => String(a.data.name || "").localeCompare(String(b.data.name || ""), "de"))
    .map((record) => `<option value="${escapeHtml(record.id)}">${escapeHtml(record.data.name || "Artikel")}</option>`)
    .join("");
  if (!articleSelect.options.length) {
    showToast("Lege zuerst einen Artikel an.", true);
    return;
  }
  document.getElementById("repair-form").reset();
  document.getElementById("repair-dialog").showModal();
}

async function saveRepair(event) {
  event.preventDefault();
  const form = document.getElementById("repair-form");
  if (!form.reportValidity()) return;
  const article = recordById(form.elements.article_id.value, "articles");
  if (!article) {
    showToast("Der ausgewählte Artikel ist nicht mehr vorhanden.", true);
    return;
  }
  const costsText = form.elements.costs.value.trim().replace(",", ".");
  const costs = Number(costsText || 0);
  if (!Number.isFinite(costs) || costs < 0) {
    showToast("Kosten müssen eine positive Zahl sein.", true);
    return;
  }
  const status = form.elements.repair_status.value;
  const date = new Date().toISOString();
  const changes = [
    syncChange("repairs", {
      article_id: article.id,
      date,
      error_description: form.elements.error_description.value.trim(),
      damage_type: form.elements.damage_type.value.trim(),
      measures: form.elements.measures.value.trim(),
      spare_parts: form.elements.spare_parts.value.trim(),
      costs,
      repair_status: status,
      completion_date: status === "Abgeschlossen" ? date : null,
      internal_notes: form.elements.internal_notes.value.trim(),
    }),
    syncChange("articles", { ...article.data, status: "In Reparatur" }, article),
    syncChange("scans", {
      timestamp: date,
      barcode: article.data.barcode || "",
      article_id: article.id,
      box_id: article.data.box_id || null,
      scan_type: "Reparatur",
      action: "Reparatur erfasst",
    }),
  ];
  try {
    await applySyncChanges(changes);
    document.getElementById("repair-dialog").close();
    showToast("Reparatur wurde eingetragen.");
  } catch (error) {
    showToast(`Reparatur konnte nicht gespeichert werden: ${error.message}`, true);
  }
}

async function moveRackArticle(articleId, boxId) {
  const article = recordById(articleId, "articles");
  const targetBox = recordById(boxId, "boxes");
  if (!article || !targetBox || article.data.box_id === targetBox.id) return;
  const previousBoxId = article.data.box_id || null;
  const changes = [
    syncChange("articles", { ...article.data, box_id: targetBox.id }, article),
    syncChange("movements", {
      article_id: article.id,
      from_box_id: previousBoxId,
      to_box_id: targetBox.id,
      quantity: 1,
      action: "Umlagern",
      timestamp: new Date().toISOString(),
    }),
  ];
  try {
    await applySyncChanges(changes);
    showToast(`${article.data.name} wurde nach ${targetBox.data.name} umgelagert.`);
  } catch (error) {
    showToast(`Umlagern fehlgeschlagen: ${error.message}`, true);
  }
}

function exportScanHistory() {
  const rows = typeRecords("scans").map((record) => {
    const article = recordById(record.data.article_id, "articles");
    return {
      timestamp: record.data.timestamp,
      barcode: record.data.barcode,
      article: article?.data.name || "",
      location: locationLabel(article?.data.box_id || record.data.box_id),
      scan_type: record.data.scan_type,
      action: record.data.action,
    };
  });
  downloadBlob(new Blob(["\ufeff", csvText(rows, [
    "timestamp", "barcode", "article", "location", "scan_type", "action",
  ])], { type: "text/csv;charset=utf-8" }), "scan-verlauf.csv");
}

function exportBackup() {
  const backup = {
    format: "eventtech-inventory-backup",
    version: 1,
    exported_at: new Date().toISOString(),
    records: records.map(({ id, entity_type, data }) => ({ id, entity_type, data })),
  };
  downloadBlob(new Blob([JSON.stringify(backup, null, 2)], { type: "application/json" }), "eventtech-backup.json");
}

async function importBackup(file) {
  if (!file || file.size > 50 * 1024 * 1024) {
    showToast("Backup fehlt oder überschreitet 50 MB.", true);
    return;
  }
  try {
    const backup = JSON.parse(await file.text());
    const allowedTypes = new Set([
      "warehouses", "racks", "levels", "boxes", "articles", "barcodes", "movements",
      "dmx_modes", "dmx_channels", "repairs", "scans", "inventories", "inventory_items",
    ]);
    if (backup?.format !== "eventtech-inventory-backup" || !Array.isArray(backup.records)) {
      throw new Error("Datei ist kein gültiges EventTech-Backup.");
    }
    if (backup.records.length > 50000) throw new Error("Backup enthält zu viele Datensätze.");
    const changes = backup.records.map((record) => {
      if (!record || typeof record.id !== "string" || !allowedTypes.has(record.entity_type) ||
          !record.data || typeof record.data !== "object" || Array.isArray(record.data)) {
        throw new Error("Backup enthält einen ungültigen Datensatz.");
      }
      const current = recordById(record.id, record.entity_type);
      return syncChange(record.entity_type, record.data, current, record.id);
    });
    if (!window.confirm(`${changes.length} Datensätze aus dem Backup zusammenführen? Vorhandene Datensätze mit derselben ID werden aktualisiert.`)) return;
    await applySyncChanges(changes);
    showToast("Backup wurde wiederhergestellt.");
  } catch (error) {
    showToast(`Backup konnte nicht importiert werden: ${error.message}`, true);
  } finally {
    elements["backup-file-input"].value = "";
  }
}

async function refreshCameraDevices() {
  if (!navigator.mediaDevices?.enumerateDevices) {
    showToast("Dein Browser unterstützt keine Kameraauswahl.", true);
    return;
  }
  let temporaryStream = null;
  try {
    temporaryStream = await navigator.mediaDevices.getUserMedia({ video: true, audio: false });
    const devices = await navigator.mediaDevices.enumerateDevices();
    const cameras = devices.filter((device) => device.kind === "videoinput");
    const selected = (readStorage("eventtech.web.settings.v1") || {}).cameraId || "";
    elements["camera-device-select"].innerHTML = [
      '<option value="">Automatisch wählen</option>',
      ...cameras.map((device, index) =>
        `<option value="${escapeHtml(device.deviceId)}">${escapeHtml(device.label || `Kamera ${index + 1}`)}</option>`,
      ),
    ].join("");
    elements["camera-device-select"].value = selected;
    showToast(`${cameras.length} Kamera${cameras.length === 1 ? "" : "s"} gefunden.`);
  } catch (error) {
    showToast(`Kameraliste konnte nicht geladen werden: ${error.message}`, true);
  } finally {
    temporaryStream?.getTracks().forEach((track) => track.stop());
  }
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
  dmxDraftModes = typeRecords("dmx_modes")
    .filter((mode) => mode.data.article_id === articleId)
    .map((mode) => ({
      id: mode.id,
      revision: mode.revision,
      name: mode.data.name || "",
      channel_count: Number(mode.data.channel_count || 1),
      channels: typeRecords("dmx_channels")
        .filter((channel) => channel.data.mode_id === mode.id)
        .sort((left, right) => Number(left.data.channel_number) - Number(right.data.channel_number))
        .map((channel) => ({
          id: channel.id,
          revision: channel.revision,
          channel_number: Number(channel.data.channel_number || 1),
          function_name: channel.data.function_name || "",
          value_min: Number(channel.data.value_min ?? 0),
          value_max: Number(channel.data.value_max ?? 255),
          description: channel.data.description || "",
        })),
    }));
  renderDmxDraft();
}

function renderDmxDraft() {
  elements["dmx-mode-list"].innerHTML = dmxDraftModes.map((mode) => `
    <article class="dmx-mode" data-dmx-mode-id="${escapeHtml(mode.id)}">
      <div class="dmx-mode-editor">
        <label>Modusname<input data-dmx-mode-name required maxlength="120" value="${escapeHtml(mode.name)}"></label>
        <label>Kanäle<input data-dmx-mode-count type="number" min="1" max="512" step="1" required value="${escapeHtml(mode.channel_count)}"></label>
        <button class="button button-quiet dmx-remove-button" type="button" data-dmx-remove-mode>Modus entfernen</button>
      </div>
      <div class="dmx-channel-editors">${mode.channels.map((channel) => `
        <div class="dmx-channel-editor" data-dmx-channel-id="${escapeHtml(channel.id)}">
          <label>CH<input data-dmx-field="channel_number" type="number" min="1" max="512" step="1" required value="${escapeHtml(channel.channel_number)}"></label>
          <label>Funktion<input data-dmx-field="function_name" required maxlength="120" value="${escapeHtml(channel.function_name)}"></label>
          <label>Min<input data-dmx-field="value_min" type="number" min="0" max="255" step="1" required value="${escapeHtml(channel.value_min)}"></label>
          <label>Max<input data-dmx-field="value_max" type="number" min="0" max="255" step="1" required value="${escapeHtml(channel.value_max)}"></label>
          <label class="dmx-description-field">Beschreibung<input data-dmx-field="description" maxlength="240" value="${escapeHtml(channel.description)}"></label>
          <button class="button button-quiet dmx-remove-button" type="button" data-dmx-remove-channel>Entfernen</button>
        </div>`).join("")}</div>
      <button class="button button-quiet dmx-add-channel-button" type="button" data-dmx-add-channel>＋ Kanal hinzufügen</button>
    </article>
  `).join("") || '<p class="dmx-empty">Noch keine DMX-Modi hinzugefügt.</p>';
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
  form.elements.auto_usage_guide.checked = hasAutoUsageGuide(data.technical_data);
  renderBoxOptions(data.box_id || "");
  renderDmx(record?.id || "");
  elements["article-attachments"].hidden = false;
  elements["article-files-input"].disabled = !record;
  elements["article-files-note"].textContent = record
    ? "Private Cloud-Dateien, sichtbar für angemeldete Benutzer dieses Inventars. Maximal 20 MB pro Datei."
    : "Speichere den Artikel zuerst, um Fotos und Dokumente hinzuzufügen.";
  elements["article-files-input"].value = "";
  elements["article-files-list"].innerHTML = record
    ? ""
    : '<p class="dmx-empty">Anhänge werden nach dem ersten Speichern verfügbar.</p>';
  if (record) renderArticleAttachments(record.id).catch((error) =>
    showToast(`Artikeldateien konnten nicht geladen werden: ${error.message}`, true));
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

function makeDmxChanges(articleId) {
  const existingModes = typeRecords("dmx_modes")
    .filter((record) => record.data.article_id === articleId);
  const existingModeIds = new Set(existingModes.map((record) => record.id));
  const existingChannels = typeRecords("dmx_channels")
    .filter((record) => existingModeIds.has(record.data.mode_id));
  const retainedModeIds = new Set();
  const retainedChannelIds = new Set();
  const changes = [];

  for (const mode of dmxDraftModes) {
    const name = String(mode.name || "").trim();
    const channelCount = Number(mode.channel_count);
    if (!name) throw new Error("Jeder DMX-Modus benötigt einen Namen.");
    if (!Number.isInteger(channelCount) || channelCount < 1 || channelCount > 512) {
      throw new Error(`Der Modus „${name}“ muss 1 bis 512 Kanäle haben.`);
    }
    if (mode.id && existingModeIds.has(mode.id)) retainedModeIds.add(mode.id);
    const existingMode = recordById(mode.id, "dmx_modes");
    if (existingMode && existingMode.data.article_id !== articleId) {
      throw new Error("Ein DMX-Modus gehört nicht zu diesem Artikel.");
    }
    const modeData = {
      ...(existingMode?.data || {}),
      article_id: articleId,
      name,
      channel_count: channelCount,
    };
    changes.push(syncChange("dmx_modes", modeData, existingMode, mode.id));

    const channelNumbers = new Set();
    for (const channel of mode.channels) {
      const channelNumber = Number(channel.channel_number);
      const valueMin = Number(channel.value_min);
      const valueMax = Number(channel.value_max);
      const functionName = String(channel.function_name || "").trim();
      if (!Number.isInteger(channelNumber) || channelNumber < 1 || channelNumber > channelCount) {
        throw new Error(`Ein Kanal liegt außerhalb des Modus „${name}“ mit ${channelCount} Kanälen.`);
      }
      if (channelNumbers.has(channelNumber)) {
        throw new Error(`Kanal ${channelNumber} ist im Modus „${name}“ doppelt vorhanden.`);
      }
      channelNumbers.add(channelNumber);
      if (!functionName) throw new Error(`Kanal ${channelNumber} benötigt eine Funktion.`);
      if (!Number.isInteger(valueMin) || !Number.isInteger(valueMax) ||
          valueMin < 0 || valueMin > valueMax || valueMax > 255) {
        throw new Error(`Der Wertebereich von Kanal ${channelNumber} muss zwischen 0 und 255 liegen.`);
      }
      const existingChannel = recordById(channel.id, "dmx_channels");
      if (existingChannel && existingChannel.data.mode_id !== mode.id) {
        throw new Error(`Kanal ${channelNumber} gehört nicht zu diesem Modus.`);
      }
      if (existingChannel) retainedChannelIds.add(existingChannel.id);
      changes.push(syncChange("dmx_channels", {
        ...(existingChannel?.data || {}),
        mode_id: mode.id,
        channel_number: channelNumber,
        function_name: functionName,
        value_min: valueMin,
        value_max: valueMax,
        description: String(channel.description || "").trim(),
      }, existingChannel, channel.id));
    }
  }

  for (const channel of existingChannels) {
    if (!retainedChannelIds.has(channel.id)) {
      changes.push(syncChange("dmx_channels", null, channel));
      changes[changes.length - 1].deleted = true;
    }
  }
  for (const mode of existingModes) {
    if (!retainedModeIds.has(mode.id)) {
      changes.push(syncChange("dmx_modes", null, mode));
      changes[changes.length - 1].deleted = true;
    }
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
    raw.technical_data = updateAutoUsageGuide(raw.technical_data, {
      enabled: elements["article-form"].elements.auto_usage_guide.checked,
      name: raw.name,
      manufacturer: raw.manufacturer,
      model: raw.model,
      category: raw.category,
    });
    const formData = validateArticleForm(raw);
    const articleChanges = makeArticleChanges(formData, activeArticle);
    const articleId = articleChanges[0].id;
    await applySyncChanges([...articleChanges, ...makeDmxChanges(articleId)]);
  } catch (error) {
    showToast(`Speichern fehlgeschlagen: ${error.message}`, true);
    saveButton.disabled = false;
    return;
  }
  const wasEdit = Boolean(activeArticle);
  elements["article-dialog"].close();
  showToast(wasEdit ? "Artikel erfolgreich aktualisiert." : "Artikel erfolgreich angelegt.");
}

function encodeStoragePath(path) {
  return path.split("/").map(encodeURIComponent).join("/");
}

async function removeArticleStorageObject(storagePath) {
  await apiRequest("/storage/v1/object/eventtech-web-attachments", {
    method: "DELETE",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ prefixes: [storagePath] }),
  });
}

async function renderArticleAttachments(articleId) {
  const attachments = await apiRequest(
    `/rest/v1/eventtech_web_attachments?select=id,name,mime_type,size_bytes,storage_path,created_at&article_id=eq.${encodeURIComponent(articleId)}&order=created_at.desc`,
  );
  if (!Array.isArray(attachments)) throw new Error("Supabase hat eine ungültige Dateiliste zurückgegeben.");
  if (activeArticle?.id !== articleId || !elements["article-dialog"].open) return;
  elements["article-files-list"].innerHTML = attachments.map((attachment) => `
    <div class="attachment-row">
      <span><strong>${escapeHtml(attachment.name)}</strong><small>${(Number(attachment.size_bytes) / 1048576).toFixed(2)} MB · ${escapeHtml(attachment.mime_type)}</small></span>
      <div>
        <button class="button button-quiet" type="button" data-download-attachment="${escapeHtml(attachment.id)}">Herunterladen</button>
        <button class="button button-quiet" type="button" data-delete-attachment="${escapeHtml(attachment.id)}">Entfernen</button>
      </div>
    </div>`).join("") || '<p class="dmx-empty">Noch keine Fotos oder Dokumente hinterlegt.</p>';
}

async function uploadArticleFiles(fileList) {
  if (!activeArticle) throw new Error("Speichere den Artikel zuerst und öffne ihn danach erneut zum Hochladen.");
  const articleId = activeArticle.id;
  const files = [...fileList];
  try {
    for (const file of files) {
      if (file.size > 20 * 1024 * 1024) {
        throw new Error(`${file.name} ist größer als 20 MB.`);
      }
      const name = file.name.replace(/[\\/:*?"<>|]/g, "_").slice(0, 180) || "Datei";
      const storagePath = `${articleId}/${crypto.randomUUID()}-${name}`;
      const encodedPath = encodeStoragePath(storagePath);
      await apiRequest(`/storage/v1/object/eventtech-web-attachments/${encodedPath}`, {
        method: "POST",
        headers: {
          "Content-Type": file.type || "application/octet-stream",
          "x-upsert": "false",
        },
        body: file,
      });
      try {
        await apiRequest("/rest/v1/eventtech_web_attachments", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Prefer: "return=minimal",
          },
          body: JSON.stringify({
            article_id: articleId,
            name,
            mime_type: file.type || "application/octet-stream",
            size_bytes: file.size,
            storage_path: storagePath,
          }),
        });
      } catch (metadataError) {
        try {
          await removeArticleStorageObject(storagePath);
        } catch (cleanupError) {
          throw new Error(`Datei wurde hochgeladen, aber Metadaten konnten nicht gespeichert werden (${metadataError.message}); Bereinigung fehlgeschlagen (${cleanupError.message}).`);
        }
        throw metadataError;
      }
    }
  } catch (uploadError) {
    if (activeArticle?.id === articleId && elements["article-dialog"].open) {
      try {
        await renderArticleAttachments(articleId);
      } catch (refreshError) {
        throw new Error(`Dateiliste konnte nach dem Upload-Fehler nicht aktualisiert werden: ${refreshError.message}; Upload-Fehler: ${uploadError.message}`);
      }
    }
    throw uploadError;
  }
  if (activeArticle?.id === articleId && elements["article-dialog"].open) {
    await renderArticleAttachments(articleId);
  }
  showToast(`${files.length} Datei${files.length === 1 ? "" : "en"} hochgeladen.`);
}

async function downloadArticleAttachment(attachmentId) {
  const articleId = activeArticle?.id;
  const result = await apiRequest(
    `/rest/v1/eventtech_web_attachments?select=id,name,mime_type,storage_path,article_id&id=eq.${encodeURIComponent(attachmentId)}`,
  );
  const attachment = Array.isArray(result) ? result[0] : null;
  if (!attachment) throw new Error("Datei wurde nicht gefunden.");
  if (!articleId || attachment.article_id !== articleId) throw new Error("Die Datei gehört nicht zum geöffneten Artikel.");
  const response = await fetch(
    `${projectConfig.url}/storage/v1/object/eventtech-web-attachments/${encodeStoragePath(attachment.storage_path)}`,
    { headers: headersFor(await validAccessToken()) },
  );
  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || `Download fehlgeschlagen (${response.status}).`);
  }
  downloadBlob(await response.blob(), attachment.name);
}

async function deleteArticleAttachment(attachmentId) {
  const articleId = activeArticle?.id;
  const result = await apiRequest(
    `/rest/v1/eventtech_web_attachments?select=id,name,storage_path,article_id&id=eq.${encodeURIComponent(attachmentId)}`,
  );
  const attachment = Array.isArray(result) ? result[0] : null;
  if (!attachment) throw new Error("Datei wurde nicht gefunden.");
  if (!articleId || attachment.article_id !== articleId) throw new Error("Die Datei gehört nicht zum geöffneten Artikel.");
  if (!window.confirm(`„${attachment.name}“ wirklich dauerhaft löschen?`)) return;
  await removeArticleStorageObject(attachment.storage_path);
  try {
    await apiRequest(`/rest/v1/eventtech_web_attachments?id=eq.${encodeURIComponent(attachment.id)}`, {
      method: "DELETE",
    });
  } catch (error) {
    throw new Error(`Datei wurde aus dem Speicher gelöscht, aber der Dateieintrag konnte nicht entfernt werden: ${error.message}`);
  }
  if (activeArticle?.id === articleId && elements["article-dialog"].open) {
    await renderArticleAttachments(articleId);
  }
  showToast("Datei wurde gelöscht.");
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
    await verifyCurrentUser();
    setAuthenticatedView(true);
    await loadCloudRecords();
    loadPendingUsers().catch((error) =>
      showToast(`Freigabeanfragen konnten nicht geladen werden: ${error.message}`, true));
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
  const profiles = await apiRequest(
    `/rest/v1/eventtech_web_profiles?select=status,role&user_id=eq.${encodeURIComponent(currentUser.id)}`,
  );
  if (!Array.isArray(profiles) || !profiles[0]) {
    throw new Error("Für dein Konto fehlt das Freigabeprofil. Bitte den Admin kontaktieren.");
  }
  currentProfile = profiles[0];
  if (currentProfile.status !== "approved") {
    throw new Error(currentProfile.status === "pending"
      ? "Dein Konto wartet noch auf die Freigabe durch den Admin."
      : "Dein Konto wurde nicht freigegeben. Bitte den Admin kontaktieren.");
  }
}

async function loadPendingUsers() {
  if (currentProfile?.role !== "admin") return;
  const users = await apiRequest("/rest/v1/rpc/eventtech_web_list_pending_users");
  if (!Array.isArray(users)) throw new Error("Supabase hat eine ungültige Kontenliste zurückgegeben.");
  elements["pending-users-list"].innerHTML = users.length
    ? users.map((user) => `
      <div class="pending-user">
        <div><strong>${escapeHtml(user.email || "E-Mail nicht verfügbar")}</strong><small>Registriert: ${escapeHtml(new Date(user.created_at).toLocaleDateString("de-DE"))}</small></div>
        <div class="feature-actions">
          <button class="button button-primary" type="button" data-user-action="approve" data-user-id="${escapeHtml(user.user_id)}">Freigeben</button>
          <button class="button button-quiet" type="button" data-user-action="deny" data-user-id="${escapeHtml(user.user_id)}">Ablehnen</button>
        </div>
      </div>`).join("")
    : '<p class="gate-footnote">Keine offenen Freigabeanfragen.</p>';
}

async function updateUserApproval(userId, approved) {
  await apiRequest("/rest/v1/rpc/eventtech_web_set_user_approval", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ p_user_id: userId, p_approved: approved }),
  });
  showToast(approved ? "Konto wurde freigegeben." : "Konto wurde abgelehnt.");
  try {
    await loadPendingUsers();
  } catch (error) {
    showToast(`Änderung gespeichert, aber Freigabeanfragen konnten nicht aktualisiert werden: ${error.message}`, true);
  }
}

async function createAccount() {
  if (!elements["login-form"].reportValidity()) return;
  const email = elements["email"].value.trim();
  const password = elements["password"].value;
  if (password.length < 12) {
    showToast("Das Passwort muss mindestens 12 Zeichen lang sein.", true);
    return;
  }
  elements["create-account-button"].disabled = true;
  try {
    await authRequest("signup", { email, password });
    elements["password"].value = "";
    showToast("Konto erstellt. Bestätige deine E-Mail; danach wartet dein Konto auf die Adminfreigabe.");
  } catch (error) {
    showToast(`Konto konnte nicht erstellt werden: ${error.message}`, true);
  } finally {
    elements["create-account-button"].disabled = false;
  }
}

async function changePassword(event) {
  event.preventDefault();
  if (!elements["change-password-form"].reportValidity()) return;
  const currentPassword = elements["current-password"].value;
  const newPassword = elements["new-password"].value;
  if (newPassword.length < 12) {
    showToast("Das neue Passwort muss mindestens 12 Zeichen lang sein.", true);
    return;
  }
  if (newPassword !== elements["confirm-password"].value) {
    showToast("Die neuen Passwörter stimmen nicht überein.", true);
    return;
  }
  try {
    const reauthenticated = await authRequest("token?grant_type=password", {
      email: currentUser.email,
      password: currentPassword,
    });
    storeSession(reauthenticated);
    await authRequest("user", { password: newPassword }, session.access_token, "PUT");
    elements["change-password-form"].reset();
    showToast("Passwort wurde aktualisiert.");
  } catch (error) {
    showToast(`Passwort konnte nicht geändert werden: ${error.message}`, true);
  }
}

async function restoreSession() {
  try {
    const savedConfig = readStorage(CONFIG_STORAGE_KEY);
    session = readStorage(SESSION_STORAGE_KEY);
    const configToLoad = savedConfig || DEFAULT_SUPABASE_CONFIG;
    try {
      saveConfig(configToLoad.url, configToLoad.anonKey);
    } catch (error) {
      showToast(`Projektkonfiguration ungültig: ${error.message}`, true);
      projectConfig = null;
    }
  } catch (error) {
    showToast(error.message, true);
    return;
  }
  if (projectConfig) setConnectionStatus("Projekt bereit · anmelden");
  if (!session?.access_token || !projectConfig) return;
  try {
    await verifyCurrentUser();
    setAuthenticatedView(true);
    await loadCloudRecords();
    loadPendingUsers().catch((error) =>
      showToast(`Freigabeanfragen konnten nicht geladen werden: ${error.message}`, true));
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

function openScanner(purpose = "search") {
  scannerPurpose = purpose;
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
  const settings = readStorage("eventtech.web.settings.v1") || {};
  const deviceId = settings.cameraId || undefined;
  if (typeof window.BarcodeDetector === "function" &&
      typeof window.BarcodeDetector.getSupportedFormats === "function") {
    const supported = await window.BarcodeDetector.getSupportedFormats();
    const preferred = ["ean_13", "ean_8", "code_128", "upc_a", "upc_e", "qr_code"];
    const formats = preferred.filter((format) => supported.includes(format));
    if (formats.length) {
      cameraDetector = new window.BarcodeDetector({ formats });
      cameraStream = await navigator.mediaDevices.getUserMedia({
        audio: false,
        video: deviceId
          ? { deviceId: { exact: deviceId } }
          : { facingMode: { ideal: "environment" } },
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
    deviceId,
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

function useScannedBarcode(rawValue, scanType = "Kamera-Scan") {
  if (scanAccepted) return;
  scanAccepted = true;
  const value = scannerValue(rawValue);
  if (!value) {
    scanAccepted = false;
    elements["scanner-message"].textContent = "Der Scan enthält keinen Barcode. Bitte erneut versuchen.";
    return;
  }
  if (scannerPurpose === "inventory") {
    elements["inventory-barcode"].value = value;
    stopScanner();
    elements["scanner-dialog"].close();
    captureInventoryScan().catch((error) => showToast(`Inventur-Scan fehlgeschlagen: ${error.message}`, true));
    return;
  }
  elements["search-input"].value = value;
  renderInventory();
  stopScanner();
  elements["scanner-dialog"].close();
  saveScan(value, scanType).catch((error) => showToast(`Scan konnte nicht gespeichert werden: ${error.message}`, true));
  const behavior = window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth";
  elements["inventory"].scrollIntoView({ behavior });
  showToast(`Suche nach Barcode ${value}`);
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
  document.querySelectorAll("[data-config-file-action]").forEach((button) => {
    button.addEventListener("click", () => openSupabaseConfigDialog(button.dataset.configFileAction));
  });
  elements["supabase-config-action"].addEventListener("click", async () => {
    if (supabaseConfigFileMode === "import") {
      elements["supabase-config-file-input"].click();
      return;
    }
    try {
      await exportSupabaseConfigFile();
      elements["supabase-config-dialog"].close();
    } catch (error) {
      showToast(`Verbindung konnte nicht exportiert werden: ${error.message}`, true);
    }
  });
  elements["supabase-config-file-input"].addEventListener("change", async (event) => {
    const file = event.target.files?.[0];
    if (!file) return;
    try {
      await importSupabaseConfigFile(file);
    } catch (error) {
      showToast(`Verbindung konnte nicht importiert werden: ${error.message}`, true);
    } finally {
      event.target.value = "";
    }
  });
  elements["supabase-config-dialog"].addEventListener("close", () => {
    elements["supabase-config-password"].value = "";
    elements["supabase-config-confirm"].value = "";
  });
  elements["login-form"].addEventListener("submit", logIn);
  elements["create-account-button"].addEventListener("click", createAccount);
  elements["logout-button"].addEventListener("click", logOut);
  elements["change-password-form"].addEventListener("submit", changePassword);
  elements["refresh-pending-users"].addEventListener("click", () => {
    loadPendingUsers().catch((error) => showToast(`Freigabeanfragen konnten nicht geladen werden: ${error.message}`, true));
  });
  elements["pending-users-list"].addEventListener("click", (event) => {
    const button = event.target.closest("[data-user-action]");
    if (!button) return;
    const approved = button.dataset.userAction === "approve";
    updateUserApproval(button.dataset.userId, approved)
      .catch((error) => showToast(`Freigabe konnte nicht gespeichert werden: ${error.message}`, true));
  });
  elements["refresh-button"].addEventListener("click", () => loadInventory(true));
  elements["search-input"].addEventListener("input", renderInventory);
  elements["status-filter"].addEventListener("change", renderInventory);
  elements["new-article-button"].addEventListener("click", () => openArticle());
  elements["empty-create-button"].addEventListener("click", () => openArticle());
  elements["article-form"].addEventListener("submit", saveArticle);
  elements["dmx-add-mode-button"].addEventListener("click", () => {
    dmxDraftModes.push({
      id: crypto.randomUUID(),
      revision: null,
      name: "",
      channel_count: 1,
      channels: [],
    });
    renderDmxDraft();
  });
  elements["article-files-input"].addEventListener("change", (event) => {
    const files = event.target.files;
    if (!files?.length) return;
    uploadArticleFiles(files)
      .catch((error) => showToast(`Dateien konnten nicht hochgeladen werden: ${error.message}`, true))
      .finally(() => { elements["article-files-input"].value = ""; });
  });
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
  elements["dashboard-scan-button"].addEventListener("click", openScanner);
  elements["scanner-dialog"].addEventListener("close", stopScanner);
  elements["manual-scan-form"].addEventListener("submit", (event) => {
    event.preventDefault();
    const value = elements["manual-barcode"].value.trim();
    if (!value) {
      showToast("Bitte scanne oder gib zuerst einen Barcode ein.", true);
      return;
    }
    useScannedBarcode(value, "Manuelle Eingabe");
  });
  elements["repair-add-button"].addEventListener("click", openRepairDialog);
  document.getElementById("repair-form").addEventListener("submit", saveRepair);
  elements["inventory-start-button"].addEventListener("click", startInventory);
  elements["inventory-scan-form"].addEventListener("submit", captureInventoryScan);
  elements["inventory-camera-button"].addEventListener("click", () => openScanner("inventory"));
  elements["inventory-finish-button"].addEventListener("click", finishInventory);
  elements["inventory-export-button"].addEventListener("click", renderInventoryItemsCsv);
  elements["inventory-session-select"].addEventListener("change", (event) => {
    currentInventoryId = event.target.value;
    renderInventoryRows();
  });
  elements["rack-warehouse-select"].addEventListener("change", renderRackGraphic);
  elements["rack-select"].addEventListener("change", renderRackGraphic);
  elements["rack-manage-button"].addEventListener("click", () => openLocationDialog("racks"));
  elements["barcode-generator-form"].addEventListener("submit", (event) => {
    event.preventDefault();
    try {
      const codeType = elements["barcode-type"].value;
      const input = elements["barcode-custom-value"].value.trim();
      let code = input || generateUniqueBarcode(codeType);
      if (codeType === "EAN-13" && /^\d{12}$/.test(code)) code += ean13CheckDigit(code);
      barcodeOptions(code, codeType);
      generatedBarcode = {
        code,
        codeType,
        label: elements["barcode-label"].value.trim(),
      };
      renderGeneratedBarcode();
    } catch (error) {
      showToast(`Barcode konnte nicht erstellt werden: ${error.message}`, true);
    }
  });
  elements["barcode-save-button"].addEventListener("click", saveGeneratedBarcode);
  elements["barcode-download-button"].addEventListener("click", downloadGeneratedBarcode);
  elements["barcode-add-pdf-button"].addEventListener("click", () => {
    if (!generatedBarcode) {
      showToast("Generiere zuerst einen Barcode.", true);
      return;
    }
    addBarcodeToPdf(generatedBarcode, Number(elements["barcode-copies"].value) || 1);
  });
  elements["barcode-pdf-button"].addEventListener("click", printBarcodePdf);
  elements["barcode-pdf-clear-button"].addEventListener("click", () => {
    barcodePdfItems = [];
    renderBarcodePdfItems();
  });
  elements["scan-search"].addEventListener("input", renderScanHistory);
  elements["scan-export-button"].addEventListener("click", exportScanHistory);
  elements["camera-refresh-button"].addEventListener("click", refreshCameraDevices);
  elements["camera-device-select"].addEventListener("change", () => {
    const settings = readStorage("eventtech.web.settings.v1") || {};
    settings.cameraId = elements["camera-device-select"].value;
    writeStorage("eventtech.web.settings.v1", settings);
  });
  const saveScannerSettings = () => {
    const settings = readStorage("eventtech.web.settings.v1") || {};
    settings.prefix = elements["scanner-prefix"].value;
    settings.suffix = elements["scanner-suffix"].value;
    writeStorage("eventtech.web.settings.v1", settings);
  };
  elements["scanner-prefix"].addEventListener("change", saveScannerSettings);
  elements["scanner-suffix"].addEventListener("change", saveScannerSettings);
  elements["backup-export-button"].addEventListener("click", exportBackup);
  elements["backup-import-button"].addEventListener("click", () => elements["backup-file-input"].click());
  elements["backup-file-input"].addEventListener("change", (event) => {
    const file = event.target.files?.[0];
    if (file) importBackup(file);
  });
  elements["settings-logout-button"].addEventListener("click", logOut);
  document.addEventListener("click", (event) => {
    const editButton = event.target.closest("[data-edit-article]");
    if (editButton) {
      const record = recordById(editButton.dataset.editArticle, "articles");
      if (record) openArticle(record);
    }
    const closeButton = event.target.closest("[data-close]");
    if (closeButton) document.getElementById(closeButton.dataset.close)?.close();
    const addBarcodeButton = event.target.closest("[data-barcode-add]");
    if (addBarcodeButton) {
      const record = recordById(addBarcodeButton.dataset.barcodeAdd, "barcodes");
      if (record) {
        addBarcodeToPdf({
          code: record.data.code,
          codeType: record.data.code_type || "Code 128",
          label: record.data.label || recordArticleName(record),
        });
      }
    }
    const editBarcodeButton = event.target.closest("[data-barcode-edit]");
    if (editBarcodeButton) editBarcodeRecord(editBarcodeButton.dataset.barcodeEdit);
    const deleteBarcodeButton = event.target.closest("[data-barcode-delete]");
    if (deleteBarcodeButton) {
      deleteBarcodeRecord(deleteBarcodeButton.dataset.barcodeDelete)
        .catch((error) => showToast(`Barcode konnte nicht gelöscht werden: ${error.message}`, true));
    }
    const removePdfButton = event.target.closest("[data-pdf-remove]");
    if (removePdfButton) {
      barcodePdfItems.splice(Number(removePdfButton.dataset.pdfRemove), 1);
      renderBarcodePdfItems();
    }
    const dmxModeAction = event.target.closest("[data-dmx-remove-mode], [data-dmx-add-channel]");
    if (dmxModeAction) {
      const modeElement = dmxModeAction.closest("[data-dmx-mode-id]");
      const mode = dmxDraftModes.find((item) => item.id === modeElement?.dataset.dmxModeId);
      if (mode && dmxModeAction.hasAttribute("data-dmx-remove-mode")) {
        dmxDraftModes = dmxDraftModes.filter((item) => item.id !== mode.id);
        renderDmxDraft();
      } else if (mode) {
        const channelCount = Number(mode.channel_count) || 1;
        const usedNumbers = new Set(mode.channels.map((channel) => Number(channel.channel_number)));
        let nextNumber = 1;
        while (usedNumbers.has(nextNumber) && nextNumber <= channelCount) nextNumber += 1;
        if (nextNumber > channelCount) {
          showToast("Alle Kanäle dieses Modus sind bereits angelegt.", true);
          return;
        }
        mode.channels.push({
          id: crypto.randomUUID(),
          revision: null,
          channel_number: nextNumber,
          function_name: "",
          value_min: 0,
          value_max: 255,
          description: "",
        });
        renderDmxDraft();
      }
    }
    const removeDmxChannel = event.target.closest("[data-dmx-remove-channel]");
    if (removeDmxChannel) {
      const modeElement = removeDmxChannel.closest("[data-dmx-mode-id]");
      const channelElement = removeDmxChannel.closest("[data-dmx-channel-id]");
      const mode = dmxDraftModes.find((item) => item.id === modeElement?.dataset.dmxModeId);
      if (mode && channelElement) {
        mode.channels = mode.channels.filter((channel) => channel.id !== channelElement.dataset.dmxChannelId);
        renderDmxDraft();
      }
    }
    const downloadAttachmentButton = event.target.closest("[data-download-attachment]");
    if (downloadAttachmentButton) {
      downloadArticleAttachment(downloadAttachmentButton.dataset.downloadAttachment)
        .catch((error) => showToast(`Datei konnte nicht heruntergeladen werden: ${error.message}`, true));
    }
    const deleteAttachmentButton = event.target.closest("[data-delete-attachment]");
    if (deleteAttachmentButton) {
      deleteArticleAttachment(deleteAttachmentButton.dataset.deleteAttachment)
        .catch((error) => showToast(`Datei konnte nicht gelöscht werden: ${error.message}`, true));
    }
  });
  document.addEventListener("input", (event) => {
    const modeElement = event.target.closest("[data-dmx-mode-id]");
    const mode = dmxDraftModes.find((item) => item.id === modeElement?.dataset.dmxModeId);
    if (!mode) return;
    if (event.target.matches("[data-dmx-mode-name]")) mode.name = event.target.value;
    else if (event.target.matches("[data-dmx-mode-count]")) mode.channel_count = event.target.value;
    else {
      const channelElement = event.target.closest("[data-dmx-channel-id]");
      const channel = mode.channels.find((item) => item.id === channelElement?.dataset.dmxChannelId);
      const field = event.target.dataset.dmxField;
      if (channel && field) channel[field] = event.target.value;
    }
  });
  document.addEventListener("change", (event) => {
    const statusSelect = event.target.closest("[data-repair-status]");
    if (statusSelect) updateRepairStatus(statusSelect.dataset.repairStatus, statusSelect.value);
  });
  document.addEventListener("dragstart", (event) => {
    const article = event.target.closest("[data-rack-article]");
    if (article) event.dataTransfer.setData("text/plain", article.dataset.rackArticle);
  });
  document.addEventListener("dragover", (event) => {
    const box = event.target.closest("[data-drop-box]");
    if (box) {
      event.preventDefault();
      box.classList.add("is-drop-target");
    }
  });
  document.addEventListener("dragleave", (event) => {
    const box = event.target.closest("[data-drop-box]");
    if (box && !box.contains(event.relatedTarget)) box.classList.remove("is-drop-target");
  });
  document.addEventListener("drop", (event) => {
    const box = event.target.closest("[data-drop-box]");
    if (!box) return;
    event.preventDefault();
    box.classList.remove("is-drop-target");
    const articleId = event.dataTransfer.getData("text/plain");
    if (articleId) moveRackArticle(articleId, box.dataset.dropBox);
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
