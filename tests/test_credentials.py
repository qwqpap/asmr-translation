"""API keys must survive a round trip and must never linger after a delete.

The store itself is platform code, so the backend-neutral logic is driven through
a fake ``keyring`` module.  One test does hit the real Windows Credential Manager,
because the ctypes layer is the part a reviewer cannot check by reading: it uses a
test-only target name so a developer's real key can never be touched.
"""

from __future__ import annotations

import contextlib
import sys
import uuid

import pytest

from asmr_lrc import credentials
from asmr_lrc.credentials import (
    CredentialError,
    environment_variable_for_role,
    roles,
    target_for_role,
)


class FakeKeyring:
    """The subset of the keyring API this module uses."""

    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}
        self.on_delete: Exception | None = None
        self.deletes: list[tuple[str, str]] = []

    def get_password(self, service: str, target: str) -> str | None:
        return self.store.get((service, target))

    def set_password(self, service: str, target: str, secret: str) -> None:
        self.store[(service, target)] = secret

    def delete_password(self, service: str, target: str) -> None:
        self.deletes.append((service, target))
        if self.on_delete is not None:
            raise self.on_delete
        if (service, target) not in self.store:
            # What a Secret Service backend raises for an absent entry.
            raise RuntimeError("No such password!")
        del self.store[(service, target)]


@pytest.fixture
def fake_keyring(monkeypatch):
    """Force the keyring path even on Windows, with a fake module behind it."""
    keyring = FakeKeyring()
    monkeypatch.setattr(credentials, "is_windows", lambda: False)
    monkeypatch.setattr(credentials, "_keyring_module", lambda: keyring)
    for role in roles():
        monkeypatch.delenv(environment_variable_for_role(role), raising=False)
    return keyring


def test_every_role_has_a_distinct_target_and_variable():
    targets = {target_for_role(role) for role in roles()}
    variables = {environment_variable_for_role(role) for role in roles()}
    assert len(targets) == len(roles())
    assert len(variables) == len(roles())


def test_native_gui_target_names_are_preserved():
    # Keys saved by the Win32 GUI must stay readable from the Qt GUI.
    assert target_for_role("draft") == "ASMRTranslation/OpenAI/Draft"
    assert target_for_role("review") == "ASMRTranslation/OpenAI/Review"


@pytest.mark.parametrize("role", ["", "openai", "Draft"])
def test_unknown_roles_are_rejected(role):
    with pytest.raises(ValueError):
        target_for_role(role)


def test_secret_round_trips(fake_keyring):
    credentials.write_secret("draft", "sk-secret")
    assert credentials.read_secret("draft") == "sk-secret"


def test_surrounding_whitespace_is_stripped(fake_keyring):
    credentials.write_secret("draft", "  sk-secret\n")
    assert fake_keyring.store[(credentials.SERVICE_NAME, target_for_role("draft"))] == "sk-secret"


def test_an_empty_secret_clears_the_entry(fake_keyring):
    credentials.write_secret("draft", "sk-secret")
    credentials.write_secret("draft", "")
    assert credentials.read_secret("draft") == ""
    assert not fake_keyring.store


def test_clearing_an_absent_entry_is_not_an_error(fake_keyring):
    credentials.delete_secret("review")
    assert fake_keyring.deletes


def test_a_failed_delete_is_reported_instead_of_swallowed(fake_keyring):
    # A user who clears the key and sees no error must not still have it stored.
    fake_keyring.store[(credentials.SERVICE_NAME, target_for_role("draft"))] = "sk-secret"
    fake_keyring.on_delete = RuntimeError("keyring is locked")
    with pytest.raises(CredentialError, match="删除系统密钥环条目失败"):
        credentials.delete_secret("draft")


def test_a_delete_that_cannot_be_confirmed_is_reported(fake_keyring, monkeypatch):
    fake_keyring.on_delete = RuntimeError("d-bus not available")

    def unreadable(*args, **kwargs):
        raise RuntimeError("d-bus not available")

    monkeypatch.setattr(fake_keyring, "get_password", unreadable)
    with pytest.raises(CredentialError):
        credentials.delete_secret("draft")


def test_a_failed_write_is_reported(fake_keyring, monkeypatch):
    def refuse(*args, **kwargs):
        raise RuntimeError("no backend")

    monkeypatch.setattr(fake_keyring, "set_password", refuse)
    with pytest.raises(CredentialError, match="写入系统密钥环失败"):
        credentials.write_secret("draft", "sk-secret")


def test_reading_falls_back_to_the_role_environment_variable(fake_keyring, monkeypatch):
    monkeypatch.setenv(environment_variable_for_role("review"), "  sk-from-env  ")
    assert credentials.read_secret("review") == "sk-from-env"


def test_a_stored_secret_wins_over_the_environment(fake_keyring, monkeypatch):
    monkeypatch.setenv(environment_variable_for_role("draft"), "sk-from-env")
    credentials.write_secret("draft", "sk-stored")
    assert credentials.read_secret("draft") == "sk-stored"


def test_reading_never_raises_when_the_store_is_broken(fake_keyring, monkeypatch):
    # The settings page reads keys while merely opening; a broken keyring must
    # degrade to the environment variable, not break the page.
    def unreadable(*args, **kwargs):
        raise RuntimeError("d-bus not available")

    monkeypatch.setattr(fake_keyring, "get_password", unreadable)
    monkeypatch.setenv(environment_variable_for_role("draft"), "sk-from-env")
    assert credentials.read_secret("draft") == "sk-from-env"


def test_backend_status_reports_a_missing_keyring_package(monkeypatch):
    monkeypatch.setattr(credentials, "is_windows", lambda: False)
    monkeypatch.setitem(sys.modules, "keyring", None)
    status = credentials.backend_status()
    assert status.name == "keyring"
    assert status.available is False


@pytest.mark.parametrize("module", ["keyring.backends.fail", "keyring.backends.null"])
def test_backend_status_rejects_the_placeholder_backends(monkeypatch, module):
    # keyring falls back to these when no desktop service answers.  Their class is
    # also called "Keyring", so only the module tells them apart -- and calling
    # either one available would promise storage that raises or discards.
    keyring = FakeKeyring()
    keyring.get_keyring = lambda: type("Keyring", (), {"__module__": module})()
    monkeypatch.setattr(credentials, "is_windows", lambda: False)
    monkeypatch.setattr(credentials, "_keyring_module", lambda: keyring)
    status = credentials.backend_status()
    assert status.available is False
    assert "gnome-keyring" in status.detail


def test_backend_status_accepts_a_real_backend(monkeypatch):
    keyring = FakeKeyring()
    keyring.get_keyring = lambda: type(
        "Keyring", (), {"__module__": "keyring.backends.SecretService"}
    )()
    monkeypatch.setattr(credentials, "is_windows", lambda: False)
    monkeypatch.setattr(credentials, "_keyring_module", lambda: keyring)
    status = credentials.backend_status()
    assert status.available is True
    # The class name alone is "Keyring" for almost every backend, so the detail
    # shown in the settings page has to carry the module.
    assert status.detail == "keyring.backends.SecretService.Keyring"


@pytest.mark.skipif(not credentials.is_windows(), reason="Credential Manager is Windows only")
def test_windows_credential_manager_round_trip():
    # A test-only target: never one of the four real role targets.
    target = f"ASMRTranslation/Test/{uuid.uuid4()}"
    try:
        credentials._windows_write(target, "sk-round-trip-日本語")
        assert credentials._windows_read(target) == "sk-round-trip-日本語"
        credentials._windows_write(target, "")
        assert credentials._windows_read(target) is None
        # Deleting what is already gone stays success.
        credentials._windows_delete(target)
    finally:
        with contextlib.suppress(CredentialError):
            credentials._windows_delete(target)
