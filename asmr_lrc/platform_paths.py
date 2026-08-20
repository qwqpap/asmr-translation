"""Cross-platform application directories.

The Win32 GUI resolved these through ``SHGetKnownFolderPath``.  This module keeps
the same Windows results while adding XDG and macOS conventions, so settings and
caches land where each desktop expects them instead of next to the executable.
"""

from __future__ import annotations

import os
import platform
import re
import sys
from functools import lru_cache
from pathlib import Path

APP_NAME = "ASMR Translation"
APP_SLUG = "asmr-translation"

_XDG_USER_DIRS = Path.home() / ".config" / "user-dirs.dirs"
_XDG_DOWNLOAD_LINE = re.compile(r'^\s*XDG_DOWNLOAD_DIR\s*=\s*"(?P<value>[^"]*)"\s*$')


def is_windows() -> bool:
    return os.name == "nt"


def is_macos() -> bool:
    return sys.platform == "darwin"


def _environment_path(name: str) -> Path | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    candidate = Path(raw).expanduser()
    return candidate if candidate.is_absolute() else None


def app_data_directory() -> Path:
    """Writable directory for settings; never inside the install tree."""
    if is_windows():
        base = _environment_path("LOCALAPPDATA") or Path.home() / "AppData" / "Local"
        return base / APP_NAME
    if is_macos():
        return Path.home() / "Library" / "Application Support" / APP_NAME
    base = _environment_path("XDG_CONFIG_HOME") or Path.home() / ".config"
    return base / APP_SLUG


def cache_directory() -> Path:
    """Writable directory for the transcription and translation caches."""
    if is_windows():
        base = _environment_path("LOCALAPPDATA") or Path.home() / "AppData" / "Local"
        return base / APP_NAME / "cache"
    if is_macos():
        return Path.home() / "Library" / "Caches" / APP_NAME
    base = _environment_path("XDG_CACHE_HOME") or Path.home() / ".cache"
    return base / APP_SLUG


def state_directory() -> Path:
    """Writable directory for logs and other non-essential runtime state."""
    if is_windows() or is_macos():
        return app_data_directory() / "state"
    base = _environment_path("XDG_STATE_HOME") or Path.home() / ".local" / "state"
    return base / APP_SLUG


def _windows_downloads_directory() -> Path | None:
    """Read the real Downloads folder, which users often move off the C: drive."""
    try:
        import winreg
    except ImportError:  # pragma: no cover - only importable on Windows
        return None
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders"
    guid = "{374DE290-123F-4565-9164-39C4925E467B}"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            raw, _ = winreg.QueryValueEx(key, guid)
    except OSError:
        return None
    text = os.path.expandvars(str(raw)).strip()
    if not text:
        return None
    candidate = Path(text)
    return candidate if candidate.is_absolute() else None


def _xdg_downloads_directory() -> Path | None:
    """Honour localized Downloads names configured by xdg-user-dirs."""
    try:
        content = _XDG_USER_DIRS.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in content.splitlines():
        match = _XDG_DOWNLOAD_LINE.fullmatch(line)
        if match is None:
            continue
        raw = match["value"].strip()
        if not raw:
            continue
        expanded = os.path.expandvars(raw.replace("$HOME", str(Path.home())))
        candidate = Path(expanded).expanduser()
        if candidate.is_absolute():
            return candidate
    return None


def downloads_directory() -> Path:
    """User Downloads folder, used as the default RJ library parent."""
    if is_windows():
        resolved = _windows_downloads_directory()
        if resolved is not None:
            return resolved
    elif not is_macos():
        resolved = _xdg_downloads_directory()
        if resolved is not None:
            return resolved
    return Path.home() / "Downloads"


def default_download_root() -> Path:
    return downloads_directory() / APP_NAME


def settings_path() -> Path:
    return app_data_directory() / "settings.json"


def player_cache_directory() -> Path:
    return cache_directory() / "player"


@lru_cache(maxsize=1)
def path_comparison_is_case_insensitive() -> bool:
    """Whether the platform treats paths as case-insensitive.

    Cache keys fold path case so that ``D:\\A.mp3`` and ``d:\\a.mp3`` share one
    entry on Windows.  On Linux those are genuinely different files, and folding
    would let one audio file silently reuse another's transcript.
    """
    return is_windows() or is_macos()


def normalize_path_for_identity(path: str) -> str:
    if path_comparison_is_case_insensitive():
        return path.casefold()
    return path


def describe_platform() -> str:
    return platform.platform()
