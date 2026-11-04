"""User-local OS credential storage. No plaintext fallback."""

import ctypes
import hashlib
import subprocess
import sys


class ModelCredentials:
    def __init__(self, identity):
        self.service = (
            "CampusInbox.ModelAPI." + hashlib.sha256(str(identity).encode()).hexdigest()[:20]
        )
        self.label = (
            "macOS 钥匙串"
            if sys.platform == "darwin"
            else "Windows 凭据管理器"
            if sys.platform == "win32"
            else "系统密钥环（需配置）"
        )

    def get(self, key):
        if sys.platform == "darwin":
            r = subprocess.run(
                ["/usr/bin/security", "find-generic-password", "-s", self.service, "-a", key, "-w"],
                capture_output=True,
                timeout=15,
                check=False,
            )
            if r.returncode == 44:
                return ""
            if r.returncode:
                raise RuntimeError("无法读取本机 API 密钥")
            return r.stdout.decode().strip()
        if sys.platform == "win32":
            return self._windows(key)
        raise RuntimeError("当前系统尚未配置安全凭据存储")

    def set(self, key, value):
        if sys.platform == "darwin":
            r = subprocess.run(
                [
                    "/usr/bin/security",
                    "add-generic-password",
                    "-U",
                    "-s",
                    self.service,
                    "-a",
                    key,
                    "-w",
                    value,
                ],
                capture_output=True,
                timeout=15,
                check=False,
            )
            if r.returncode:
                raise RuntimeError("无法保存本机 API 密钥")
        elif sys.platform == "win32":
            self._windows(key, value)
        else:
            raise RuntimeError("当前系统尚未配置安全凭据存储")

    def _windows(self, key, value=None):
        from ctypes import wintypes as w

        class Credential(ctypes.Structure):
            _fields_ = [
                ("Flags", w.DWORD),
                ("Type", w.DWORD),
                ("TargetName", w.LPWSTR),
                ("Comment", w.LPWSTR),
                ("LastWritten", w.FILETIME),
                ("CredentialBlobSize", w.DWORD),
                ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
                ("Persist", w.DWORD),
                ("AttributeCount", w.DWORD),
                ("Attributes", w.LPVOID),
                ("TargetAlias", w.LPWSTR),
                ("UserName", w.LPWSTR),
            ]

        api = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
        target = self.service + ":" + key
        if value is not None:
            blob = ctypes.create_string_buffer(value.encode())
            item = Credential(
                Type=1,
                TargetName=target,
                CredentialBlobSize=len(value.encode()),
                CredentialBlob=ctypes.cast(blob, ctypes.POINTER(ctypes.c_ubyte)),
                Persist=2,
                UserName="CampusInbox",
            )
            api.CredWriteW.argtypes = [ctypes.POINTER(Credential), w.DWORD]
            api.CredWriteW.restype = w.BOOL
            if not api.CredWriteW(ctypes.byref(item), 0):
                raise RuntimeError("无法保存 Windows API 凭据")
            return None
        item = ctypes.POINTER(Credential)()
        api.CredReadW.argtypes = [
            w.LPCWSTR,
            w.DWORD,
            w.DWORD,
            ctypes.POINTER(ctypes.POINTER(Credential)),
        ]
        api.CredReadW.restype = w.BOOL
        if not api.CredReadW(target, 1, 0, ctypes.byref(item)):
            if ctypes.get_last_error() == 1168:
                return ""
            raise RuntimeError("无法读取 Windows API 凭据")
        api.CredFree.argtypes = [w.LPVOID]
        api.CredFree.restype = None
        try:
            if item.contents.CredentialBlobSize > 8192:
                raise RuntimeError("凭据格式无效")
            return ctypes.string_at(
                item.contents.CredentialBlob, item.contents.CredentialBlobSize
            ).decode()
        finally:
            api.CredFree(item)
