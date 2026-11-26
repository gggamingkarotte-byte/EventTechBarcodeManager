import base64
import json
import os
from urllib.parse import urlsplit

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC


_FORMAT = "eventtech-supabase-config"
_VERSION = 1
_KDF = "PBKDF2-SHA256"
_CIPHER = "AES-256-GCM"
_ITERATIONS = 310_000
_AAD = b"eventtech-supabase-config:v1"
_MAX_FILE_SIZE = 64 * 1024
_MIN_PASSWORD_LENGTH = 12


def _validate_settings(settings: dict) -> dict:
    if not isinstance(settings, dict):
        raise ValueError("Die Konfigurationsdatei enthält ungültige Daten.")

    project_url = settings.get("project_url")
    public_key = settings.get("public_key")
    shared_key = settings.get("shared_key")
    if not isinstance(project_url, str) or not isinstance(public_key, str):
        raise ValueError("Projekt-URL oder öffentlicher Schlüssel fehlen.")
    parsed_url = urlsplit(project_url)
    if (
        parsed_url.scheme != "https"
        or not parsed_url.hostname
        or parsed_url.username
        or parsed_url.password
        or parsed_url.query
        or parsed_url.fragment
        or parsed_url.path not in ("", "/")
    ):
        raise ValueError("Die Supabase-Projekt-URL muss eine gültige HTTPS-Adresse sein.")
    if len(public_key.strip()) < 20:
        raise ValueError("Der öffentliche Supabase-Schlüssel ist ungültig.")

    result = {
        "format": _FORMAT,
        "version": _VERSION,
        "project_url": f"https://{parsed_url.netloc}",
        "public_key": public_key.strip(),
    }
    if shared_key is not None:
        if not isinstance(shared_key, str) or len(shared_key) < 32:
            raise ValueError("Der Organisationsschlüssel muss mindestens 32 Zeichen lang sein.")
        result["shared_key"] = shared_key
    return result


def _derive_key(password: str, salt: bytes) -> bytes:
    if not isinstance(password, str) or len(password) < _MIN_PASSWORD_LENGTH:
        raise ValueError("Das Dateipasswort muss mindestens 12 Zeichen lang sein.")
    return PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=_ITERATIONS,
    ).derive(password.encode("utf-8"))


def encrypt_supabase_config(settings: dict, password: str) -> bytes:
    payload = json.dumps(
        _validate_settings(settings),
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    salt = os.urandom(16)
    nonce = os.urandom(12)
    ciphertext = AESGCM(_derive_key(password, salt)).encrypt(nonce, payload, _AAD)
    envelope = {
        "format": _FORMAT,
        "version": _VERSION,
        "cipher": _CIPHER,
        "kdf": _KDF,
        "iterations": _ITERATIONS,
        "salt": base64.b64encode(salt).decode("ascii"),
        "nonce": base64.b64encode(nonce).decode("ascii"),
        "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
    }
    return json.dumps(envelope, indent=2).encode("utf-8")


def decrypt_supabase_config(file_data: bytes, password: str) -> dict:
    if not isinstance(file_data, bytes) or len(file_data) > _MAX_FILE_SIZE:
        raise ValueError("Die Konfigurationsdatei fehlt oder ist zu groß.")
    try:
        envelope = json.loads(file_data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Die Datei ist keine gültige Supabase-Konfiguration.") from exc
    if (
        not isinstance(envelope, dict)
        or envelope.get("format") != _FORMAT
        or envelope.get("version") != _VERSION
        or envelope.get("cipher") != _CIPHER
        or envelope.get("kdf") != _KDF
        or envelope.get("iterations") != _ITERATIONS
    ):
        raise ValueError("Dateiformat oder Verschlüsselungsverfahren wird nicht unterstützt.")

    try:
        salt = base64.b64decode(envelope["salt"], validate=True)
        nonce = base64.b64decode(envelope["nonce"], validate=True)
        ciphertext = base64.b64decode(envelope["ciphertext"], validate=True)
        if len(salt) != 16 or len(nonce) != 12 or len(ciphertext) > _MAX_FILE_SIZE:
            raise ValueError("Die Konfigurationsdatei ist beschädigt.")
        plaintext = AESGCM(_derive_key(password, salt)).decrypt(nonce, ciphertext, _AAD)
        settings = json.loads(plaintext.decode("utf-8"))
    except InvalidTag as exc:
        raise ValueError("Dateipasswort ist falsch oder die Datei wurde verändert.") from exc
    except (KeyError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Die Konfigurationsdatei ist beschädigt.") from exc
    return _validate_settings(settings)
