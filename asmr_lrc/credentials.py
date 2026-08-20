"""Cross-platform secret storage for external API keys.

Secrets never enter settings.json, caches, logs, or child-process command lines.

On Windows this talks to the same Credential Manager entries the Win32 GUI used
(``ASMRTranslation/OpenAI/Draft`` and ``.../Review``, generic credentials holding
a UTF-16LE blob), so keys saved by either GUI remain readable by the other.  On
Linux and macOS it delegates to ``keyring``, which brokers the Secret Service
(GNOME Keyring / KWallet) and the macOS Keychain respectively.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from .platform_paths import is_windows

SERVICE_NAME = "ASMRTranslation"
_ROLE_TARGETS = {
    "draft": "ASMRTranslation/OpenAI/Draft",
    "review": "ASMRTranslation/OpenAI/Review",
    "analysis": "ASMRTranslation/OpenAI/Analysis",
    "fallback": "ASMRTranslation/OpenAI/Fallback",
}
_ROLE_ENVIRONMENT = {
    "draft": "ASMR_TRANSLATION_API_KEY",
    "review": "ASMR_TRANSLATION_REVIEW_API_KEY",
    "analysis": "ASMR_TRANSLATION_ANALYSIS_API_KEY",
    "fallback": "ASMR_TRANSLATION_FALLBACK_API_KEY",
}

_CRED_TYPE_GENERIC = 1
_CRED_PERSIST_LOCAL_MACHINE = 2
# CredDeleteW fails with this when the entry is simply not there, which is the
# one failure that means success.  Anything else must reach the user: silently
# swallowing it would leave a key the user believes they deleted.
_ERROR_NOT_FOUND = 1168

# keyring falls back to these when no desktop service answers: fail raises on
# every operation, null accepts writes and drops them.
_UNUSABLE_BACKENDS = ("keyring.backends.fail", "keyring.backends.null")


class CredentialError(RuntimeError):
    """The platform secret store is unavailable or refused the operation."""


@dataclass(frozen=True, slots=True)
class BackendStatus:
    name: str
    available: bool
    detail: str


def target_for_role(role: str) -> str:
    try:
        return _ROLE_TARGETS[role]
    except KeyError as exc:
        raise ValueError(f"未知的凭据角色: {role}") from exc


def environment_variable_for_role(role: str) -> str:
    try:
        return _ROLE_ENVIRONMENT[role]
    except KeyError as exc:
        raise ValueError(f"未知的凭据角色: {role}") from exc


# --- Windows Credential Manager via ctypes ---------------------------------


def _windows_structures():  # pragma: no cover - exercised only on Windows
    import ctypes
    from ctypes import wintypes

    class FILETIME(ctypes.Structure):
        _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]

    class CREDENTIALW(ctypes.Structure):
        _fields_ = [
            ("Flags", wintypes.DWORD),
            ("Type", wintypes.DWORD),
            ("TargetName", wintypes.LPWSTR),
            ("Comment", wintypes.LPWSTR),
            ("LastWritten", FILETIME),
            ("CredentialBlobSize", wintypes.DWORD),
            ("CredentialBlob", ctypes.POINTER(ctypes.c_byte)),
            ("Persist", wintypes.DWORD),
            ("AttributeCount", wintypes.DWORD),
            ("Attributes", ctypes.c_void_p),
            ("TargetAlias", wintypes.LPWSTR),
            ("UserName", wintypes.LPWSTR),
        ]

    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    advapi.CredReadW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.POINTER(CREDENTIALW)),
    ]
    advapi.CredReadW.restype = wintypes.BOOL
    advapi.CredWriteW.argtypes = [ctypes.POINTER(CREDENTIALW), wintypes.DWORD]
    advapi.CredWriteW.restype = wintypes.BOOL
    advapi.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    advapi.CredDeleteW.restype = wintypes.BOOL
    advapi.CredFree.argtypes = [ctypes.c_void_p]
    advapi.CredFree.restype = None
    return ctypes, advapi, CREDENTIALW


def _windows_read(target: str) -> str | None:  # pragma: no cover - Windows only
    ctypes, advapi, CREDENTIALW = _windows_structures()
    pointer = ctypes.POINTER(CREDENTIALW)()
    if not advapi.CredReadW(target, _CRED_TYPE_GENERIC, 0, ctypes.byref(pointer)):
        return None
    try:
        blob = pointer.contents
        size = int(blob.CredentialBlobSize)
        if size <= 0:
            return ""
        raw = ctypes.string_at(blob.CredentialBlob, size)
        return raw.decode("utf-16-le", errors="replace")
    finally:
        advapi.CredFree(ctypes.cast(pointer, ctypes.c_void_p))


def _windows_write(target: str, secret: str) -> None:  # pragma: no cover - Windows only
    ctypes, advapi, CREDENTIALW = _windows_structures()
    if not secret:
        _windows_delete(target)
        return
    encoded = secret.encode("utf-16-le")
    buffer = (ctypes.c_byte * len(encoded)).from_buffer_copy(encoded)
    credential = CREDENTIALW()
    credential.Type = _CRED_TYPE_GENERIC
    credential.TargetName = target
    credential.CredentialBlobSize = len(encoded)
    credential.CredentialBlob = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))
    credential.Persist = _CRED_PERSIST_LOCAL_MACHINE
    credential.UserName = SERVICE_NAME
    if not advapi.CredWriteW(ctypes.byref(credential), 0):
        code = ctypes.get_last_error()
        raise CredentialError(f"写入 Windows 凭据管理器失败（错误码 {code}）")


def _windows_delete(target: str) -> None:  # pragma: no cover - Windows only
    """Remove the entry. Already absent is success; any other failure is not."""
    ctypes, advapi, _ = _windows_structures()
    if advapi.CredDeleteW(target, _CRED_TYPE_GENERIC, 0):
        return
    code = ctypes.get_last_error()
    if code == _ERROR_NOT_FOUND:
        return
    raise CredentialError(f"删除 Windows 凭据管理器条目失败（错误码 {code}）")


# --- keyring for Secret Service and Keychain -------------------------------


def _keyring_module():
    try:
        import keyring
    except ImportError as exc:
        raise CredentialError(
            "缺少 keyring：请执行 `pip install keyring` 以使用系统密钥环，"
            "或改用环境变量提供 API Key。"
        ) from exc
    return keyring


def _keyring_read(target: str) -> str | None:
    keyring = _keyring_module()
    try:
        return keyring.get_password(SERVICE_NAME, target)
    except Exception as exc:  # keyring raises backend-specific errors
        raise CredentialError(f"读取系统密钥环失败: {exc}") from exc


def _keyring_write(target: str, secret: str) -> None:
    keyring = _keyring_module()
    if not secret:
        _keyring_delete(target)
        return
    try:
        keyring.set_password(SERVICE_NAME, target, secret)
    except Exception as exc:
        raise CredentialError(f"写入系统密钥环失败: {exc}") from exc


def _keyring_delete(target: str) -> None:
    """Remove the entry. Already absent is success; any other failure is not."""
    keyring = _keyring_module()
    try:
        keyring.delete_password(SERVICE_NAME, target)
    except Exception as exc:
        # Backends disagree on which exception means "no such password", so
        # confirm by reading instead of guessing from the type.  If the secret is
        # still readable -- or the store is too broken to say -- report failure:
        # a key the user believes they cleared must never linger silently.
        try:
            removed = not keyring.get_password(SERVICE_NAME, target)
        except Exception:
            removed = False
        if not removed:
            raise CredentialError(f"删除系统密钥环条目失败: {exc}") from exc


# --- Public API ------------------------------------------------------------


def backend_status() -> BackendStatus:
    if is_windows():
        try:
            _windows_structures()
        except OSError as exc:  # pragma: no cover - advapi32 always present
            return BackendStatus("wincred", False, str(exc))
        return BackendStatus("wincred", True, "Windows 凭据管理器")
    try:
        keyring = _keyring_module()
    except CredentialError as exc:
        return BackendStatus("keyring", False, str(exc))
    try:
        backend = type(keyring.get_keyring())
    except Exception as exc:
        return BackendStatus("keyring", False, f"无可用后端: {exc}")
    # Every backend class is named "Keyring" or similar, so the module decides:
    # keyring.backends.fail raises on use and keyring.backends.null discards
    # silently.  Both would let the settings page promise storage it has not got.
    module = getattr(backend, "__module__", "")
    if any(module.startswith(unusable) for unusable in _UNUSABLE_BACKENDS):
        return BackendStatus(
            "keyring",
            False,
            "系统未提供密钥环服务（缺少 Secret Service / KWallet）；"
            "请安装 gnome-keyring 或 kwalletmanager，或改用环境变量。",
        )
    name = f"{module}.{backend.__name__}" if module else backend.__name__
    return BackendStatus("keyring", True, name)


def read_secret(role: str) -> str:
    """Return the stored key, falling back to the role's environment variable."""
    target = target_for_role(role)
    stored: str | None = None
    try:
        stored = _windows_read(target) if is_windows() else _keyring_read(target)
    except CredentialError:
        stored = None
    if stored:
        return stored
    return os.environ.get(environment_variable_for_role(role), "").strip()


def write_secret(role: str, secret: str) -> None:
    """Persist or clear the key. An empty secret deletes the stored entry."""
    target = target_for_role(role)
    cleaned = secret.strip()
    if is_windows():
        _windows_write(target, cleaned)
    else:
        _keyring_write(target, cleaned)


def delete_secret(role: str) -> None:
    target = target_for_role(role)
    if is_windows():
        _windows_delete(target)
    else:
        _keyring_delete(target)


def roles() -> tuple[str, ...]:
    return tuple(_ROLE_TARGETS)
