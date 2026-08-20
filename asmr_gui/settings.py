"""Settings model shared byte-for-byte with the native Windows GUI.

The JSON schema here is deliberately identical to ``native/src/settings.cpp``:
same keys, same nesting, same defaults.  A user who has been running the Win32
build keeps their configuration when they switch to this GUI, and can switch
back, because both read and write ``<app data>/settings.json``.

``python_path`` is kept for that round-trip even though this GUI runs in the
interpreter itself -- dropping the key would silently reset it for the other
front-end.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

from asmr_lrc.config import DEFAULT_ANALYSIS_MODEL, DEFAULT_TRANSLATION_MODEL
from asmr_lrc.platform_paths import (
    app_data_directory,
    default_download_root,
    settings_path,
)

DEFAULT_ENDPOINT = "https://api.asmr-200.com"
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"


@dataclass(slots=True)
class ProviderSettings:
    kind: str = "ollama"
    base_url: str = DEFAULT_OLLAMA_URL
    model: str = DEFAULT_TRANSLATION_MODEL
    strict_schema: bool = True
    protocol: str = "chat-json"

    @classmethod
    def parse(cls, data: object, fallback: ProviderSettings) -> ProviderSettings:
        if not isinstance(data, dict):
            return replace(fallback)
        model = str(data.get("model", fallback.model))
        protocol = data.get("protocol")
        if not isinstance(protocol, str) or not protocol:
            protocol = "translategemma" if model.startswith("translategemma:") else "chat-json"
        return cls(
            kind=str(data.get("kind", fallback.kind)),
            base_url=str(data.get("base_url", fallback.base_url)),
            model=model,
            strict_schema=bool(data.get("strict_schema", fallback.strict_schema)),
            protocol=protocol,
        )

    def to_worker_dict(self, api_key: str = "") -> dict[str, Any]:
        """Payload for :mod:`asmr_lrc.session`; the key travels in memory only."""
        payload: dict[str, Any] = {
            "kind": self.kind,
            "base_url": self.base_url,
            "model": self.model,
            "strict_schema": self.strict_schema,
            "protocol": self.protocol,
        }
        if self.kind == "openai":
            payload["api_key"] = api_key
        return payload


def _find_asr_model(project_root: Path) -> str:
    for relative in ("models/faster-whisper-large-v3", "models/large-v3"):
        candidate = project_root / relative
        if (candidate / "model.bin").is_file():
            return str(candidate)
    return "large-v3"


def _project_root() -> Path:
    """Locate the checkout, so a source install finds its models and cache."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").is_file():
            return parent
    return Path.cwd()


def _default_ffmpeg() -> str:
    return shutil.which("ffmpeg") or "ffmpeg"


@dataclass(slots=True)
class AppSettings:
    python_path: str = ""
    asr_model: str = "large-v3"
    ffmpeg_path: str = "ffmpeg"
    cache_root: str = ""
    glossary_path: str = ""
    download_root: str = ""
    download_endpoint: str = DEFAULT_ENDPOINT
    curl_path: str = ""
    download_proxy: str = ""
    download_connect_timeout: int = 10
    download_notice_shown: bool = False
    setup_prompted: bool = False
    setup_completed: bool = False
    review_same_as_draft: bool = False
    review_enabled: bool = False
    analysis_enabled: bool = False
    fallback_enabled: bool = False
    quality_mode: bool = True
    draft: ProviderSettings = field(default_factory=ProviderSettings)
    review: ProviderSettings = field(default_factory=ProviderSettings)
    analysis: ProviderSettings = field(default_factory=ProviderSettings)
    fallback: ProviderSettings = field(default_factory=ProviderSettings)

    @classmethod
    def defaults(cls) -> AppSettings:
        project_root = _project_root()
        draft = ProviderSettings(
            model=DEFAULT_TRANSLATION_MODEL,
            protocol="translategemma",
        )
        analysis = ProviderSettings(model=DEFAULT_ANALYSIS_MODEL, protocol="chat-json")
        glossary = project_root / "glossary.json"
        return cls(
            python_path=sys.executable,
            asr_model=_find_asr_model(project_root),
            ffmpeg_path=_default_ffmpeg(),
            cache_root=str(project_root / ".cache"),
            glossary_path=str(glossary) if glossary.is_file() else "",
            download_root=str(default_download_root()),
            draft=draft,
            review=replace(analysis),
            analysis=analysis,
            fallback=replace(analysis),
            analysis_enabled=True,
            fallback_enabled=True,
            review_enabled=False,
        )

    @classmethod
    def parse(cls, data: dict[str, Any], defaults: AppSettings) -> AppSettings:
        def text(key: str, current: str) -> str:
            value = data.get(key)
            return str(value) if isinstance(value, str) else current

        def flag(key: str, current: bool) -> bool:
            value = data.get(key)
            return bool(value) if isinstance(value, bool) else current

        timeout = defaults.download_connect_timeout
        raw_timeout = data.get("download_connect_timeout")
        if isinstance(raw_timeout, int | float) and int(raw_timeout) > 0:
            timeout = int(raw_timeout)
        draft = ProviderSettings.parse(data.get("draft"), defaults.draft)
        review_enabled = (
            bool(data["review_enabled"])
            if isinstance(data.get("review_enabled"), bool)
            else draft.protocol != "translategemma"
        )
        return cls(
            python_path=text("python_path", defaults.python_path),
            asr_model=text("asr_model", defaults.asr_model),
            ffmpeg_path=text("ffmpeg_path", defaults.ffmpeg_path),
            cache_root=text("cache_root", defaults.cache_root),
            glossary_path=text("glossary_path", defaults.glossary_path),
            download_root=text("download_root", defaults.download_root),
            download_endpoint=text("download_endpoint", defaults.download_endpoint),
            curl_path=text("curl_path", defaults.curl_path),
            download_proxy=text("download_proxy", defaults.download_proxy),
            download_connect_timeout=timeout,
            download_notice_shown=flag("download_notice_shown", defaults.download_notice_shown),
            setup_prompted=flag("setup_prompted", defaults.setup_prompted),
            setup_completed=flag("setup_completed", defaults.setup_completed),
            review_same_as_draft=flag("review_same_as_draft", defaults.review_same_as_draft),
            review_enabled=review_enabled,
            analysis_enabled=flag("analysis_enabled", defaults.analysis_enabled),
            fallback_enabled=flag("fallback_enabled", defaults.fallback_enabled),
            quality_mode=flag("quality_mode", defaults.quality_mode),
            draft=draft,
            review=ProviderSettings.parse(data.get("review"), defaults.review),
            analysis=ProviderSettings.parse(data.get("analysis"), defaults.analysis),
            fallback=ProviderSettings.parse(data.get("fallback"), defaults.fallback),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    # --- derived values ----------------------------------------------------

    def cache_root_path(self) -> Path:
        return Path(self.cache_root or (_project_root() / ".cache")).expanduser()

    def download_root_path(self) -> Path:
        return Path(self.download_root or default_download_root()).expanduser()

    def worker_config(
        self, secrets: dict[str, str], *, overwrite: bool = False
    ) -> dict[str, Any]:
        """Build the session config exactly as the native GUI did.

        Secrets are looked up per role and passed in memory; they are never
        written to ``settings.json`` and never appear in an event payload.
        """
        config: dict[str, Any] = {
            "asr_model": self.asr_model,
            "cache_root": str(self.cache_root_path()),
            "ffmpeg_path": self.ffmpeg_path,
            "glossary_path": self.glossary_path,
            "quality_mode": "quality" if self.quality_mode else "balanced",
            "review_enabled": self.review_enabled,
            "overwrite": overwrite,
            "batch_size": 12,
            "context_before": 8,
            "context_after": 8,
            "prompt_character_limit": 24_000,
            "draft_provider": self.draft.to_worker_dict(secrets.get("draft", "")),
        }
        if self.review_same_as_draft:
            config["review_provider"] = "same"
        else:
            config["review_provider"] = self.review.to_worker_dict(secrets.get("review", ""))
        if self.analysis_enabled and self.analysis.model:
            config["analysis_provider"] = self.analysis.to_worker_dict(
                secrets.get("analysis", "")
            )
        if self.fallback_enabled and self.fallback.model:
            config["fallback_provider"] = self.fallback.to_worker_dict(
                secrets.get("fallback", "")
            )
        return config

    def download_settings(self) -> dict[str, Any]:
        return {
            "download_endpoint": self.download_endpoint,
            "curl_path": self.curl_path,
            "download_proxy": self.download_proxy,
            "download_connect_timeout": self.download_connect_timeout,
        }


def load_settings() -> AppSettings:
    """Read settings, falling back to defaults on any corruption."""
    defaults = AppSettings.defaults()
    path = settings_path()
    if not path.is_file():
        return defaults
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return defaults
    if not isinstance(data, dict):
        return defaults
    return AppSettings.parse(data, defaults)


def save_settings(settings: AppSettings) -> Path:
    """Write settings atomically so a crash cannot truncate the file."""
    path = settings_path()
    app_data_directory().mkdir(parents=True, exist_ok=True)
    payload = json.dumps(settings.to_dict(), ensure_ascii=False, indent=2)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(payload, encoding="utf-8", newline="\n")
    os.replace(temporary, path)
    return path
