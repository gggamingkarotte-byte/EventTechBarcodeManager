const FORMAT = "eventtech-supabase-config";
const VERSION = 1;
const CIPHER = "AES-256-GCM";
const KDF = "PBKDF2-SHA256";
const ITERATIONS = 310000;
const AAD = new TextEncoder().encode("eventtech-supabase-config:v1");
const MAX_FILE_SIZE = 64 * 1024;
const MIN_PASSWORD_LENGTH = 12;

export const DEFAULT_SUPABASE_CONFIG = Object.freeze({
  url: "https://gqybdurfhnuovygamsqu.supabase.co",
  anonKey: "sb_publishable_C-laH-9z6fqzKcZhfKKEvQ_xgf8ssKK",
});

function toBase64(bytes) {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary);
}

function fromBase64(value) {
  if (typeof value !== "string") throw new Error("Die Konfigurationsdatei ist beschädigt.");
  const binary = atob(value);
  return Uint8Array.from(binary, (character) => character.charCodeAt(0));
}

function validateSettings(settings) {
  if (!settings || typeof settings !== "object" || Array.isArray(settings)) {
    throw new Error("Die Konfigurationsdatei enthält ungültige Daten.");
  }
  if (typeof settings.project_url !== "string" || typeof settings.public_key !== "string") {
    throw new Error("Projekt-URL oder öffentlicher Schlüssel fehlen.");
  }
  const url = new URL(settings.project_url);
  if (url.protocol !== "https:" || url.username || url.password || url.search || url.hash ||
      (url.pathname !== "/" && url.pathname !== "")) {
    throw new Error("Die Supabase-Projekt-URL muss eine gültige HTTPS-Adresse sein.");
  }
  const publicKey = settings.public_key.trim();
  if (publicKey.length < 20) throw new Error("Der öffentliche Supabase-Schlüssel ist ungültig.");

  const validated = {
    format: FORMAT,
    version: VERSION,
    project_url: url.origin,
    public_key: publicKey,
  };
  if (settings.shared_key !== undefined) {
    if (typeof settings.shared_key !== "string" || settings.shared_key.length < 32) {
      throw new Error("Der Organisationsschlüssel muss mindestens 32 Zeichen lang sein.");
    }
    validated.shared_key = settings.shared_key;
  }
  return validated;
}

async function deriveKey(password, salt) {
  if (typeof password !== "string" || password.length < MIN_PASSWORD_LENGTH) {
    throw new Error("Das Dateipasswort muss mindestens 12 Zeichen lang sein.");
  }
  if (!globalThis.crypto?.subtle) {
    throw new Error("Verschlüsselte Konfigurationsdateien benötigen eine sichere HTTPS-Verbindung.");
  }
  const material = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(password),
    "PBKDF2",
    false,
    ["deriveKey"],
  );
  return crypto.subtle.deriveKey(
    { name: "PBKDF2", salt, iterations: ITERATIONS, hash: "SHA-256" },
    material,
    { name: "AES-GCM", length: 256 },
    false,
    ["encrypt", "decrypt"],
  );
}

export async function encryptSupabaseConfig(settings, password) {
  const payload = new TextEncoder().encode(JSON.stringify(validateSettings(settings)));
  const salt = crypto.getRandomValues(new Uint8Array(16));
  const nonce = crypto.getRandomValues(new Uint8Array(12));
  const key = await deriveKey(password, salt);
  const ciphertext = await crypto.subtle.encrypt(
    { name: "AES-GCM", iv: nonce, additionalData: AAD, tagLength: 128 },
    key,
    payload,
  );
  return {
    format: FORMAT,
    version: VERSION,
    cipher: CIPHER,
    kdf: KDF,
    iterations: ITERATIONS,
    salt: toBase64(salt),
    nonce: toBase64(nonce),
    ciphertext: toBase64(new Uint8Array(ciphertext)),
  };
}

export async function decryptSupabaseConfig(fileText, password) {
  if (typeof fileText !== "string" || new TextEncoder().encode(fileText).length > MAX_FILE_SIZE) {
    throw new Error("Die Konfigurationsdatei fehlt oder ist zu groß.");
  }
  let envelope;
  try {
    envelope = JSON.parse(fileText);
  } catch {
    throw new Error("Die Datei ist keine gültige Supabase-Konfiguration.");
  }
  if (!envelope || envelope.format !== FORMAT || envelope.version !== VERSION ||
      envelope.cipher !== CIPHER || envelope.kdf !== KDF || envelope.iterations !== ITERATIONS) {
    throw new Error("Dateiformat oder Verschlüsselungsverfahren wird nicht unterstützt.");
  }

  try {
    const salt = fromBase64(envelope.salt);
    const nonce = fromBase64(envelope.nonce);
    const ciphertext = fromBase64(envelope.ciphertext);
    if (salt.length !== 16 || nonce.length !== 12 || ciphertext.length > MAX_FILE_SIZE) {
      throw new Error("Die Konfigurationsdatei ist beschädigt.");
    }
    const key = await deriveKey(password, salt);
    const plaintext = await crypto.subtle.decrypt(
      { name: "AES-GCM", iv: nonce, additionalData: AAD, tagLength: 128 },
      key,
      ciphertext,
    );
    return validateSettings(JSON.parse(new TextDecoder().decode(plaintext)));
  } catch (error) {
    if (error instanceof Error && error.message.startsWith("Das Dateipasswort")) throw error;
    if (error instanceof Error && error.message.includes("sichere HTTPS")) throw error;
    if (error instanceof Error && error.message.startsWith("Dateiformat")) throw error;
    if (error instanceof Error && error.message.startsWith("Die Konfigurationsdatei")) throw error;
    if (error?.name === "OperationError") {
      throw new Error("Dateipasswort ist falsch oder die Datei wurde verändert.");
    }
    throw new Error(`Konfigurationsdatei konnte nicht entschlüsselt werden: ${error.message}`);
  }
}
