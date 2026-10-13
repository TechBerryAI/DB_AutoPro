"""Encrypt small secrets with Windows DPAPI for the current user.

CryptProtectData is called without CRYPTPROTECT_LOCAL_MACHINE, so a blob can
be decrypted only by the same Windows user. A fixed application entropy value
is included. It is not a password; it stops other programs that do not know
this value from calling CryptUnprotectData on the blob.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

# Do not show a Windows credential prompt if decryption is not possible.
_CRYPTPROTECT_UI_FORBIDDEN = 0x01
_DESCRIPTION = "DB AutoPro saved server"
_ENTROPY = b"DB_AutoPro.saved-server.v1"


class CredentialProtectionError(Exception):
    """A saved credential could not be protected or read."""


class _DataBlob(ctypes.Structure):
    _fields_ = (
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_char)),
    )


def _blob(data: bytes) -> tuple[_DataBlob, ctypes.Array]:
    buffer = ctypes.create_string_buffer(data, len(data))
    blob = _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    return blob, buffer


def _crypt32():
    try:
        library = ctypes.windll.crypt32
        kernel = ctypes.windll.kernel32
    except AttributeError as exc:
        raise CredentialProtectionError("Windows DPAPI is not available on this computer.") from exc
    library.CryptProtectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        wintypes.LPCWSTR,
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    library.CryptProtectData.restype = wintypes.BOOL
    library.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    library.CryptUnprotectData.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    return library, kernel


def protect_bytes(plain: bytes) -> bytes:
    """Return a DPAPI blob bound to the current Windows user."""
    if not isinstance(plain, bytes):
        raise CredentialProtectionError("Only bytes can be protected.")
    library, kernel = _crypt32()
    incoming, _incoming_buf = _blob(plain)
    entropy, _entropy_buf = _blob(_ENTROPY)
    outgoing = _DataBlob()
    ok = library.CryptProtectData(
        ctypes.byref(incoming),
        _DESCRIPTION,
        ctypes.byref(entropy),
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(outgoing),
    )
    if not ok:
        raise CredentialProtectionError("Windows could not protect this credential.")
    try:
        return ctypes.string_at(outgoing.pbData, outgoing.cbData)
    finally:
        kernel.LocalFree(outgoing.pbData)


def unprotect_bytes(blob: bytes) -> bytes:
    """Decrypt a blob created by protect_bytes for this Windows user."""
    if not isinstance(blob, bytes) or not blob:
        raise CredentialProtectionError("Saved credential is not valid protected data.")
    library, kernel = _crypt32()
    incoming, _incoming_buf = _blob(blob)
    entropy, _entropy_buf = _blob(_ENTROPY)
    outgoing = _DataBlob()
    ok = library.CryptUnprotectData(
        ctypes.byref(incoming),
        None,
        ctypes.byref(entropy),
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(outgoing),
    )
    if not ok:
        raise CredentialProtectionError(
            "Saved credential could not be decrypted for this Windows user."
        )
    try:
        return ctypes.string_at(outgoing.pbData, outgoing.cbData)
    finally:
        kernel.LocalFree(outgoing.pbData)
