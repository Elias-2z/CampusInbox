"""OS vault contracts and native Windows tests, using temporary synthetic targets only."""

import ctypes
import hashlib
import subprocess
import sys
from types import SimpleNamespace

import pytest

from campus_inbox.connectors import onebot
from campus_inbox.connectors.credentials import WindowsCredentials
from campus_inbox.connectors.onebot import BridgeUpdate, Keychain, OneBotAdapter

TOKEN = "synthetic-qq-access-1234567890"
SECRET = "synthetic-qq-events-1234567890"


def test_platform_factory_keeps_existing_mac_namespace(tmp_path, monkeypatch):
    expected = "CampusInbox.QQ." + hashlib.sha256(str(tmp_path.resolve()).encode()).hexdigest()[:16]
    monkeypatch.setattr(sys, "platform", "darwin")
    store = onebot.credential_store(tmp_path)
    assert isinstance(store, Keychain)
    assert store.service == expected
    assert store.supported
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout=TOKEN.encode())

    monkeypatch.setattr(onebot.subprocess, "run", run)
    store.set("access", TOKEN)
    assert store.get("access") == TOKEN
    assert calls[0][0] == "/usr/bin/security"
    assert calls[0][calls[0].index("-s") + 1] == expected
    monkeypatch.setattr(sys, "platform", "win32")
    assert isinstance(onebot.credential_store(tmp_path), WindowsCredentials)


def test_windows_scope_and_safe_failures(tmp_path, monkeypatch):
    first = WindowsCredentials(tmp_path / "first")
    second = WindowsCredentials(tmp_path / "second")
    assert first.service != second.service
    assert first.service == WindowsCredentials(tmp_path / "first").service

    def fail(*args):
        raise OSError(TOKEN)

    monkeypatch.setattr(WindowsCredentials, "_windows", fail)
    for operation in (lambda: first.get("access"), lambda: first.set("access", TOKEN)):
        with pytest.raises(RuntimeError) as caught:
            operation()
        assert TOKEN not in str(caught.value)
    bridge = OneBotAdapter(tmp_path, secrets=first)
    with pytest.raises(RuntimeError):
        bridge.configure(BridgeUpdate(enabled=True, access_token=TOKEN, event_secret=SECRET))
    assert not bridge.path.exists()
    assert not bridge.config.enabled


@pytest.mark.skipif(sys.platform != "win32", reason="Uses the real current-user Windows vault")
def test_native_windows_vault_restart_isolation_and_no_plaintext(tmp_path):
    stores = [WindowsCredentials(tmp_path), WindowsCredentials(tmp_path / "other")]
    try:
        assert stores[0].get("access") == ""
        bridge = OneBotAdapter(tmp_path)
        assert isinstance(bridge.secrets, WindowsCredentials)
        bridge.configure(BridgeUpdate(enabled=True, access_token=TOKEN, event_secret=SECRET))
        assert bridge.public()["credential_storage"] == "Windows 凭据管理器"
        assert TOKEN not in bridge.path.read_text() and SECRET not in bridge.path.read_text()
        reopened = OneBotAdapter(tmp_path)
        assert reopened.secret("access") == TOKEN
        assert reopened.secret("events") == SECRET
        reopened.configure(BridgeUpdate(enabled=True))
        assert reopened.secret("access") == TOKEN
        assert stores[1].get("access") == ""
        # A new interpreter has no adapter token cache and must read the OS vault.
        subprocess.run(
            [
                sys.executable,
                "-c",
                "from pathlib import Path; import sys; "
                "from campus_inbox.connectors.onebot import OneBotAdapter; "
                "b=OneBotAdapter(Path(sys.argv[1])); "
                f"assert b.secret('access') == {TOKEN!r}; "
                f"assert b.secret('events') == {SECRET!r}",
                str(tmp_path),
            ],
            check=True,
            capture_output=True,
            timeout=15,
        )
        # A synthetically corrupted blob must fail closed, never be returned as text.
        stores[0]._windows("access", "corrupt")
        with pytest.raises(RuntimeError, match="Windows QQ"):
            OneBotAdapter(tmp_path).secret("access")
        stores[0].set("access", TOKEN)
        assert OneBotAdapter(tmp_path).secret("access") == TOKEN
    finally:
        from ctypes import wintypes

        api = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
        api.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
        api.CredDeleteW.restype = wintypes.BOOL
        for store in stores:
            for key in ("access", "events"):
                if not api.CredDeleteW(store.service + ":" + key, 1, 0):
                    assert ctypes.get_last_error() == 1168
