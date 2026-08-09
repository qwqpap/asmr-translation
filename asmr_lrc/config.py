import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from .providers import ProviderConfig
from .translation_context import CONTEXT_PROMPT_VERSION, GlossaryTerm

SUPPORTED_AUDIO_EXTENSIONS = frozenset({".mp3", ".m4a", ".flac", ".wav", ".opus", ".ogg", ".aac"})


@dataclass(frozen=True, slots=True)
class FilterConfig:
    hallucination_phrases: tuple[str, ...] = (
        "ご視聴ありがとうございました",
        "ご覧いただきありがとうございました",
        "チャンネル登録",
        "高評価お願いします",
        "字幕提供",
    )
    high_no_speech_probability: float = 0.90
    minimum_non_speech_duration: float = 1.0
    long_silence_seconds: float = 8.0
    repeated_text_limit: int = 2
    max_line_characters: int = 32
    merge_gap_seconds: float = 0.35


@dataclass(frozen=True, slots=True)
class AppConfig:
    cache_root: Path
    asr_model: str = "large-v3"
    fallback_asr_model: str | None = None
    device: str = "cuda"
    compute_type: str = "int8_float16"
    language: str = "ja"
    ffmpeg_path: str = "ffmpeg"
    ollama_model: str = "qwen3.5-9b-abliterated:latest"
    ollama_url: str = "http://127.0.0.1:11434"
    # Keep the model warm within the translation phase; pipeline unloads it once at the end.
    ollama_keep_alive: str = "5m"
    translation_batch_size: int = 12
    translation_retries: int = 2
    translation_prompt_character_limit: int = 24_000
    quality_mode: str = "quality"
    context_before: int = 8
    context_after: int = 8
    review_enabled: bool = True
    draft_provider: ProviderConfig | None = None
    review_provider: ProviderConfig | None = None
    pinned_glossary: tuple[GlossaryTerm, ...] = ()
    overwrite: bool = False
    filter: FilterConfig = field(default_factory=FilterConfig)

    def __post_init__(self) -> None:
        if self.language != "ja":
            raise ValueError("第一版仅支持固定 language=ja")
        if not self.ffmpeg_path.strip():
            raise ValueError("ffmpeg_path 不能为空")
        if self.translation_batch_size < 1:
            raise ValueError("translation_batch_size 必须大于 0")
        if self.translation_retries < 0:
            raise ValueError("translation_retries 不能小于 0")
        if self.translation_prompt_character_limit < 2_000:
            raise ValueError("translation_prompt_character_limit 不能小于 2000")
        if self.quality_mode not in {"quality", "balanced"}:
            raise ValueError("quality_mode 必须是 quality 或 balanced")
        if self.context_before < 0 or self.context_after < 0:
            raise ValueError("上下文行数不能小于 0")
        if self.draft_provider is None:
            object.__setattr__(
                self,
                "draft_provider",
                ProviderConfig(
                    kind="ollama",
                    base_url=self.ollama_url,
                    model=self.ollama_model,
                    keep_alive=self.ollama_keep_alive,
                ),
            )
        if self.review_enabled and self.review_provider is None:
            object.__setattr__(self, "review_provider", self.draft_provider)

    def translation_profile_id(self) -> str:
        assert self.draft_provider is not None
        payload = {
            "prompt_version": CONTEXT_PROMPT_VERSION,
            "quality_mode": self.quality_mode,
            "batch_size": self.translation_batch_size,
            "context_before": self.context_before,
            "context_after": self.context_after,
            "prompt_character_limit": self.translation_prompt_character_limit,
            "review_enabled": self.review_enabled,
            "draft_provider": self.draft_provider.cache_identity(),
            "review_provider": (
                None if self.review_provider is None else self.review_provider.cache_identity()
            ),
            "pinned_glossary": [term.to_dict() for term in self.pinned_glossary],
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()
