import base64
import ctypes
import sys
import uuid
from ctypes import wintypes


_KEYRING_SERVICE = "EventTech Barcode Manager"
_KEYRING_PREFIX = "keyring:v1:"


def secret_store_name() -> str:
    if sys.platform == "win32":
        return "Windows DPAPI"
    if sys.platform.startswith("linux"):
        return "GNOME Keyring"
    return "nicht unterstütztem Schlüsselbund"


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


def _windows_dpapi(data: bytes, decrypt: bool) -> bytes:
    if sys.platform != "win32":
        raise RuntimeError("Der geschützte Schlüssel-Speicher ist nur unter Windows verfügbar.")

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    blob_pointer = ctypes.POINTER(_DataBlob)
    crypt32.CryptProtectData.argtypes = [
        blob_pointer, wintypes.LPCWSTR, blob_pointer, ctypes.c_void_p, ctypes.c_void_p,
        wintypes.DWORD, blob_pointer,
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    crypt32.CryptUnprotectData.argtypes = [
        blob_pointer, ctypes.c_void_p, blob_pointer, ctypes.c_void_p, ctypes.c_void_p,
        wintypes.DWORD, blob_pointer,
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    input_buffer = ctypes.create_string_buffer(data)
    input_blob = _DataBlob(
        len(data),
        ctypes.cast(input_buffer, ctypes.POINTER(ctypes.c_ubyte)),
    )
    output_blob = _DataBlob()
    flags = 0x1
    if decrypt:
        success = crypt32.CryptUnprotectData(
            ctypes.byref(input_blob), None, None, None, None, flags, ctypes.byref(output_blob)
        )
    else:
        success = crypt32.CryptProtectData(
            ctypes.byref(input_blob), "EventTech Barcode Manager", None, None, None,
            flags, ctypes.byref(output_blob)
        )
    if not success:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        kernel32.LocalFree(output_blob.pbData)


def _linux_keyring():
    import keyring
    from keyring.backends.SecretService import Keyring

    keyring.set_keyring(Keyring())
    return keyring


def protect_secret(value: str) -> str:
    if sys.platform == "win32":
        encrypted = _windows_dpapi(value.encode("utf-8"), decrypt=False)
        return base64.b64encode(encrypted).decode("ascii")
    if sys.platform.startswith("linux"):
        reference = uuid.uuid4().hex
        try:
            keyring = _linux_keyring()
            keyring.set_password(_KEYRING_SERVICE, reference, value)
        except Exception as exc:
            raise RuntimeError(
                "Der Linux-Schlüsselbund ist nicht verfügbar. Starten Sie die App in einer "
                "Desktop-Sitzung mit entsperrtem GNOME Keyring."
            ) from exc
        return f"{_KEYRING_PREFIX}{reference}"
    raise RuntimeError(f"Geschützte Schlüssel werden auf {sys.platform} nicht unterstützt.")


def unprotect_secret(value: str) -> str:
    if value.startswith(_KEYRING_PREFIX):
        if not sys.platform.startswith("linux"):
            raise RuntimeError("Dieser Schlüssel wurde in einem Linux-Schlüsselbund gespeichert.")
        reference = value[len(_KEYRING_PREFIX):]
        try:
            keyring = _linux_keyring()
            secret = keyring.get_password(_KEYRING_SERVICE, reference)
        except Exception as exc:
            raise RuntimeError(
                "Der Linux-Schlüsselbund ist nicht verfügbar oder nicht entsperrt."
            ) from exc
        if secret is None:
            raise RuntimeError(
                "Der Schlüssel wurde im Linux-Schlüsselbund nicht gefunden. "
                "Bitte geben Sie den Organisationsschlüssel erneut ein."
            )
        return secret
    if sys.platform == "win32":
        encrypted = base64.b64decode(value.encode("ascii"), validate=True)
        return _windows_dpapi(encrypted, decrypt=True).decode("utf-8")
    raise RuntimeError(
        "Dieser Schlüssel wurde unter Windows mit DPAPI geschützt und kann hier nicht "
        "entschlüsselt werden. Bitte geben Sie ihn erneut ein."
    )
