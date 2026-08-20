"""The Qt GUI must read and write the same settings.json as the native build.

``native/src/settings.cpp`` is the schema of record.  If a field is added on one
side only, a user switching front-ends silently loses it, so the key sets are
asserted here rather than left to review.  The file *location* matters just as
much: a renamed application folder would give the two front-ends a settings file
each and no error message.
"""

import json
import re
from pathlib import Path

import pytest

from asmr_gui import settings as gui_settings
from asmr_gui.settings import AppSettings, ProviderSettings
from asmr_lrc.platform_paths import APP_NAME

NATIVE_SETTINGS_SOURCE = Path(__file__).parents[1] / "native" / "src" / "settings.cpp"

# Exactly the keys native/src/settings.cpp reads and writes.
NATIVE_KEYS = {
    "python_path",
    "asr_model",
    "ffmpeg_path",
    "cache_root",
    "glossary_path",
    "download_root",
    "download_endpoint",
    "curl_path",
    "download_proxy",
    "download_connect_timeout",
    "download_notice_shown",
    "setup_prompted",
    "setup_completed",
    "review_same_as_draft",
    "review_enabled",
    "analysis_enabled",
    "fallback_enabled",
    "quality_mode",
    "draft",
    "review",
    "analysis",
    "fallback",
}
NATIVE_PROVIDER_KEYS = {"kind", "base_url", "model", "strict_schema", "protocol"}


@pytest.fixture
def settings_file(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    monkeypatch.setattr(gui_settings, "settings_path", lambda: path)
    monkeypatch.setattr(gui_settings, "app_data_directory", lambda: tmp_path)
    return path


def native_document() -> dict:
    """A settings.json as the Win32 GUI writes it."""
    return {
        "python_path": "C:\\project\\.venv\\Scripts\\python.exe",
        "asr_model": "C:\\project\\models\\faster-whisper-large-v3",
        "ffmpeg_path": "C:\\ffmpeg\\bin\\ffmpeg.exe",
        "cache_root": "C:\\project\\.cache",
        "glossary_path": "",
        "download_root": "D:\\Downloads\\ASMR Translation",
        "download_endpoint": "https://api.asmr-200.com",
        "curl_path": "",
        "download_proxy": "",
        "download_connect_timeout": 15,
        "download_notice_shown": True,
        "setup_prompted": True,
        "setup_completed": True,
        "review_same_as_draft": False,
        "review_enabled": False,
        "analysis_enabled": False,
        "fallback_enabled": True,
        "quality_mode": True,
        "draft": {
            "kind": "ollama",
            "base_url": "http://127.0.0.1:11434",
            "model": "translategemma:4b",
            "strict_schema": True,
            "protocol": "translategemma",
        },
        "review": {
            "kind": "ollama",
            "base_url": "http://127.0.0.1:11434",
            "model": "qwen3.5-9b-abliterated:latest",
            "strict_schema": True,
            "protocol": "chat-json",
        },
        "analysis": {
            "kind": "ollama",
            "base_url": "http://127.0.0.1:11434",
            "model": "qwen3.5-9b-abliterated:latest",
            "strict_schema": True,
            "protocol": "chat-json",
        },
        "fallback": {
            "kind": "ollama",
            "base_url": "http://127.0.0.1:11434",
            "model": "qwen3.5-9b-abliterated:latest",
            "strict_schema": True,
            "protocol": "chat-json",
        },
    }


def test_written_document_matches_the_native_schema(settings_file):
    path = gui_settings.save_settings(AppSettings.defaults())
    document = json.loads(path.read_text(encoding="utf-8"))
    assert set(document) == NATIVE_KEYS
    for role in ("draft", "review", "analysis", "fallback"):
        assert set(document[role]) == NATIVE_PROVIDER_KEYS


def test_native_document_round_trips_unchanged(settings_file):
    original = native_document()
    settings_file.write_text(json.dumps(original, indent=2), encoding="utf-8")
    gui_settings.save_settings(gui_settings.load_settings())
    assert json.loads(settings_file.read_text(encoding="utf-8")) == original


def test_load_falls_back_to_defaults_on_corruption(settings_file):
    settings_file.write_text("{ this is not json", encoding="utf-8")
    assert gui_settings.load_settings() == AppSettings.defaults()


def test_byte_order_mark_is_tolerated(settings_file):
    document = native_document()
    settings_file.write_bytes(
        b"\xef\xbb\xbf" + json.dumps(document).encode("utf-8")
    )
    assert gui_settings.load_settings().asr_model == document["asr_model"]


def test_translategemma_model_selects_its_own_protocol():
    provider = ProviderSettings.parse({"model": "translategemma:4b"}, ProviderSettings())
    assert provider.protocol == "translategemma"


def test_explicit_protocol_wins_over_the_model_name():
    provider = ProviderSettings.parse(
        {"model": "translategemma:4b", "protocol": "chat-json"}, ProviderSettings()
    )
    assert provider.protocol == "chat-json"


def test_review_defaults_off_for_the_translategemma_protocol(settings_file):
    document = native_document()
    del document["review_enabled"]
    settings_file.write_text(json.dumps(document), encoding="utf-8")
    assert gui_settings.load_settings().review_enabled is False


def test_api_key_travels_only_for_openai_providers():
    ollama = ProviderSettings(kind="ollama")
    openai = ProviderSettings(kind="openai", model="gpt-4o-mini")
    assert "api_key" not in ollama.to_worker_dict("secret")
    assert openai.to_worker_dict("secret")["api_key"] == "secret"


def test_worker_config_never_carries_a_key_for_local_providers():
    settings = AppSettings.parse(native_document(), AppSettings.defaults())
    config = settings.worker_config({role: "secret" for role in ("draft", "review", "fallback")})
    assert "secret" not in json.dumps(config)


def test_worker_config_omits_disabled_roles():
    settings = AppSettings.parse(native_document(), AppSettings.defaults())
    config = settings.worker_config({})
    # analysis_enabled is false in the document, fallback_enabled is true.
    assert "analysis_provider" not in config
    assert config["fallback_provider"]["model"] == "qwen3.5-9b-abliterated:latest"
    assert config["quality_mode"] == "quality"


def test_review_provider_is_shared_when_requested():
    document = native_document() | {"review_same_as_draft": True}
    settings = AppSettings.parse(document, AppSettings.defaults())
    assert settings.worker_config({})["review_provider"] == "same"


def first_wide_literal_after(source: str, anchor: str) -> str:
    """The first ``L"..."`` literal following ``anchor`` in a C++ source file."""
    start = source.index(anchor)
    match = re.compile(r'L"(?P<value>[^"]*)"').search(source, start)
    assert match is not None, f"no wide literal after {anchor!r}"
    return match["value"]


@pytest.mark.skipif(
    not NATIVE_SETTINGS_SOURCE.is_file(), reason="native sources are not in this checkout"
)
def test_windows_settings_location_matches_the_native_gui():
    source = NATIVE_SETTINGS_SOURCE.read_text(encoding="utf-8")
    # Python builds LOCALAPPDATA / APP_NAME / "settings.json"; the native GUI
    # builds SHGetKnownFolderPath(FOLDERID_LocalAppData) / <literal> / <literal>.
    assert first_wide_literal_after(source, "LocalAppDataRoot() {") == APP_NAME
    assert first_wide_literal_after(source, "SettingsPath() {") == "settings.json"


@pytest.mark.skipif(
    not NATIVE_SETTINGS_SOURCE.is_file(), reason="native sources are not in this checkout"
)
def test_default_download_library_matches_the_native_gui():
    source = NATIVE_SETTINGS_SOURCE.read_text(encoding="utf-8")
    # Both front-ends default to <Downloads>/APP_NAME, so an existing library
    # keeps being found after switching front-ends.
    assert first_wide_literal_after(source, "DefaultDownloadRoot() {") == APP_NAME
