from dataclasses import dataclass, field
from pathlib import Path

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
    ollama_model: str = "qwen3.5-9b-abliterated:latest"
    ollama_url: str = "http://127.0.0.1:11434"
    # Keep the model warm within the translation phase; pipeline unloads it once at the end.
    ollama_keep_alive: str = "5m"
    translation_batch_size: int = 12
    translation_retries: int = 2
    overwrite: bool = False
    filter: FilterConfig = field(default_factory=FilterConfig)

    def __post_init__(self) -> None:
        if self.language != "ja":
            raise ValueError("第一版仅支持固定 language=ja")
        if self.translation_batch_size < 1:
            raise ValueError("translation_batch_size 必须大于 0")
        if self.translation_retries < 0:
            raise ValueError("translation_retries 不能小于 0")
