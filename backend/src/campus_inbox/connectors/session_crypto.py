"""Windows DPAPI; opt-in Mac Chaoxing sessions use a Keychain-backed AES-GCM key."""

import base64
import ctypes
import hashlib
import os
import sys
from ctypes import wintypes
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from campus_inbox.model_credentials import ModelCredentials


class Blob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def _transform(data, decrypt=False, entropy_value=b"courseinbox-chaoxing-session-v1"):
    if os.name != "nt":
        raise OSError("Windows DPAPI required")
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = Blob()
    entropy_buffer = ctypes.create_string_buffer(entropy_value)
    entropy = Blob(
        len(entropy_buffer.raw) - 1, ctypes.cast(entropy_buffer, ctypes.POINTER(ctypes.c_ubyte))
    )
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    if decrypt:
        ok = crypt.CryptUnprotectData(
            ctypes.byref(source), None, ctypes.byref(entropy), None, None, 1, ctypes.byref(target)
        )
    else:
        ok = crypt.CryptProtectData(
            ctypes.byref(source), None, ctypes.byref(entropy), None, None, 1, ctypes.byref(target)
        )
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree.restype = ctypes.c_void_p
        kernel.LocalFree(target.data)


MAC_HEADER = b"CampusInbox.Chaoxing.mac.v1\x00"


def mac_key(identity, *, create=False):
    """Never put cookies in the keychain or share keys with the model API/QQ vault."""
    store = ModelCredentials(identity)
    store.service = (
        "CampusInbox.Chaoxing."
        + hashlib.sha256(str(Path(identity).resolve()).encode()).hexdigest()[:20]
    )
    try:
        value = store.get("session-encryption-v1")
        if not value and create:
            value = base64.urlsafe_b64encode(os.urandom(32)).decode("ascii")
            store.set("session-encryption-v1", value)
        key = base64.b64decode(value, altchars=b"-_", validate=True)
        if len(key) != 32:
            raise ValueError()
        return key
    except Exception:
        raise RuntimeError("无法读取或保存学习通钥匙串密钥，请检查权限并重新扫码。") from None


def mac_context(identity, entropy):
    return MAC_HEADER + hashlib.sha256(str(Path(identity).resolve()).encode() + entropy).digest()


def seal(data, *, entropy=b"courseinbox-chaoxing-session-v1", identity=None):
    if sys.platform == "darwin" and identity is not None:
        nonce = os.urandom(12)
        return (
            MAC_HEADER
            + nonce
            + AESGCM(mac_key(identity, create=True)).encrypt(
                nonce, data, mac_context(identity, entropy)
            )
        )
    return _transform(data, entropy_value=entropy)


def unseal(data, *, entropy=b"courseinbox-chaoxing-session-v1", identity=None):
    if sys.platform == "darwin" and identity is not None:
        try:
            if not data.startswith(MAC_HEADER) or len(data) < len(MAC_HEADER) + 28:
                raise ValueError()
            payload = data[len(MAC_HEADER) :]
            return AESGCM(mac_key(identity)).decrypt(
                payload[:12], payload[12:], mac_context(identity, entropy)
            )
        except Exception:
            raise RuntimeError("本机学习通登录状态无法解密，请检查钥匙串权限或重新扫码。") from None
    return _transform(data, True, entropy_value=entropy)
