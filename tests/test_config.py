from pathlib import Path

from asmr_lrc.config import AppConfig
from asmr_lrc.providers import ProviderConfig


def test_new_default_roles_are_explicit_and_review_is_off(tmp_path: Path) -> None:
    config = AppConfig(cache_root=tmp_path)

    assert config.draft_provider is not None
    assert config.draft_provider.model == "translategemma:4b"
    assert config.draft_provider.protocol == "translategemma"
    assert config.analysis_provider is not None
    assert config.analysis_provider.model == "qwen3.5-9b-abliterated:latest"
    assert config.fallback_provider == config.analysis_provider
    assert config.review_enabled is False


def test_legacy_qwen_provider_keeps_single_model_roles(tmp_path: Path) -> None:
    legacy = ProviderConfig(
        "ollama",
        "http://127.0.0.1:11434",
        "qwen3.5-9b-abliterated:latest",
    )
    config = AppConfig(cache_root=tmp_path, draft_provider=legacy)

    assert legacy.protocol == "chat-json"
    assert config.analysis_provider is config.draft_provider
    assert config.fallback_provider is config.draft_provider
    assert config.review_enabled is True
    assert config.review_provider is config.draft_provider


def test_protocol_and_model_roles_isolate_translation_profile(tmp_path: Path) -> None:
    translategemma = AppConfig(cache_root=tmp_path)
    legacy = AppConfig(
        cache_root=tmp_path,
        draft_provider=ProviderConfig(
            "ollama",
            "http://127.0.0.1:11434",
            "qwen3.5-9b-abliterated:latest",
        ),
    )

    assert translategemma.translation_profile_id() != legacy.translation_profile_id()
