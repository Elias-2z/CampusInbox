"""Windows QQ credentials use the existing OS vault binding, in a separate namespace."""

import hashlib
from pathlib import Path

from campus_inbox.model_credentials import ModelCredentials


class WindowsCredentials(ModelCredentials):
    """Generic credentials belong to the current Windows user and this data directory.

    Reuse the tested CredRead/CredWrite binding without sharing Model API targets.
    No token is written to CampusInbox configuration or passed to a subprocess.
    """

    label = "Windows 凭据管理器"
    supported = True

    def __init__(self, identity):
        directory = str(Path(identity).resolve()).casefold()
        self.service = "CampusInbox.QQ." + hashlib.sha256(directory.encode()).hexdigest()[:20]

    def get(self, key):
        try:
            value = self._windows(key)
            if value and (
                not 24 <= len(value) <= 2048 or any(ord(c) < 33 or ord(c) > 126 for c in value)
            ):
                raise ValueError("Invalid stored QQ credential")
            return value
        except Exception:
            raise RuntimeError("无法读取 Windows QQ 凭据，请重新配置或检查系统凭据权限。") from None

    def set(self, key, value):
        try:
            self._windows(key, value)
        except Exception:
            raise RuntimeError("无法保存 Windows QQ 凭据，请检查系统凭据权限。") from None
