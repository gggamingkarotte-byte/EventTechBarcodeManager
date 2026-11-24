import base64
import ctypes
import sys
from ctypes import wintypes


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


def protect_secret(value: str) -> str:
    encrypted = _windows_dpapi(value.encode("utf-8"), decrypt=False)
    return base64.b64encode(encrypted).decode("ascii")


def unprotect_secret(value: str) -> str:
    encrypted = base64.b64decode(value.encode("ascii"), validate=True)
    return _windows_dpapi(encrypted, decrypt=True).decode("utf-8")
