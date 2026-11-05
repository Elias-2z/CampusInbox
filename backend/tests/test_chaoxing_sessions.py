"""Synthetic-only Mac session storage and backwards-compatible Windows encryption."""

import json
import sys

import pytest

from campus_inbox.connectors import session_crypto as crypto


class Vault:
    values = {}

    def __init__(self, identity):
        self.service = ""

    def get(self, name):
        return self.values.get((self.service, name), "")

    def set(self, name, value):
        self.values[self.service, name] = value


@pytest.fixture
def mac(monkeypatch):
    Vault.values = {}
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(crypto, "ModelCredentials", Vault)
    return Vault


def test_mac_sessions_are_authenticated_encrypted_and_survive_new_instances(mac, tmp_path):
    body = json.dumps({"cookies": [{"name": "test", "value": "synthetic-session-cookie"}]}).encode()
    first = crypto.seal(body, identity=tmp_path)
    second = crypto.seal(body, identity=tmp_path)
    assert first != second
    assert body not in first and b"synthetic-session-cookie" not in first
    assert crypto.unseal(first, identity=tmp_path) == body
    assert crypto.unseal(second, identity=tmp_path) == body
    assert len(mac.values) == 1
    assert next(iter(mac.values))[0].startswith("CampusInbox.Chaoxing.")


@pytest.mark.parametrize("change", ["tamper", "directory", "entropy", "missing_key", "old_format"])
def test_mac_session_corruption_and_wrong_context_fail_closed(mac, tmp_path, change):
    body = b'{"cookies":[]}'
    encrypted = crypto.seal(body, identity=tmp_path)
    identity, entropy = tmp_path, b"courseinbox-chaoxing-session-v1"
    if change == "tamper":
        encrypted = encrypted[:-1] + bytes([encrypted[-1] ^ 1])
    elif change == "directory":
        identity = tmp_path / "another-account-directory"
    elif change == "entropy":
        entropy = b"different-protocol"
    elif change == "missing_key":
        mac.values.clear()
    elif change == "old_format":
        encrypted = body
    saved = dict(mac.values)
    with pytest.raises(RuntimeError, match="重新扫码"):
        crypto.unseal(encrypted, identity=identity, entropy=entropy)
    assert mac.values == saved  # A read never creates or replaces vault keys.


def test_corrupt_vault_key_is_not_replaced_or_exposed(mac, tmp_path):
    crypto.seal(b"test", identity=tmp_path)
    target = next(iter(mac.values))
    mac.values[target] = "invalid-private-key"
    with pytest.raises(RuntimeError) as error:
        crypto.seal(b"test", identity=tmp_path)
    assert "invalid-private-key" not in str(error.value)
    assert mac.values[target] == "invalid-private-key"


def test_keychain_failure_does_not_leak_cookie_or_vault_error(mac, monkeypatch, tmp_path):
    def fail(*args):
        raise OSError("synthetic-sensitive-vault-error")

    monkeypatch.setattr(Vault, "get", fail)
    with pytest.raises(RuntimeError) as error:
        crypto.seal(b"synthetic-cookie", identity=tmp_path)
    assert "synthetic" not in str(error.value)
    assert not mac.values


def test_windows_cipher_format_and_entropy_stay_unchanged(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(sys, "platform", "win32")

    def transform(data, decrypt=False, entropy_value=b""):
        calls.append((data, decrypt, entropy_value))
        return b"windows-dpapi"

    monkeypatch.setattr(crypto, "_transform", transform)
    assert crypto.seal(b"session", identity=tmp_path) == b"windows-dpapi"
    assert crypto.unseal(b"old-session", identity=tmp_path) == b"windows-dpapi"
    assert calls == [
        (b"session", False, b"courseinbox-chaoxing-session-v1"),
        (b"old-session", True, b"courseinbox-chaoxing-session-v1"),
    ]


def test_mac_without_chaoxing_identity_does_not_change_windows_only_push_crypto(mac, monkeypatch):
    calls = []

    def legacy(data, decrypt=False, entropy_value=b""):
        calls.append((data, entropy_value))
        return b"legacy-encrypted"

    monkeypatch.setattr(crypto, "_transform", legacy)
    assert crypto.seal(b"push-token", entropy=b"pushplus") == b"legacy-encrypted"
    assert calls == [(b"push-token", b"pushplus")]
    assert not mac.values
