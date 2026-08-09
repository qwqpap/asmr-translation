from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
TRANSLATION_SCHEMA_VERSION = 2


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{name} 必须是数字")
    return float(value)


@dataclass(frozen=True, slots=True)
class WordTiming:
    start: float
    end: float
    word: str
    probability: float | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WordTiming:
        return cls(
            start=_number(data["start"], "word.start"),
            end=_number(data["end"], "word.end"),
            word=str(data["word"]),
            probability=(
                None
                if data.get("probability") is None
                else _number(data["probability"], "word.probability")
            ),
        )


@dataclass(frozen=True, slots=True)
class Segment:
    id: str
    start: float
    end: float
    text: str
    avg_logprob: float | None = None
    no_speech_prob: float | None = None
    words: tuple[WordTiming, ...] = ()

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("segment.id 不能为空")
        if self.start < 0 or self.end < self.start:
            raise ValueError(f"片段 {self.id} 时间范围无效")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Segment:
        return cls(
            id=str(data["id"]),
            start=_number(data["start"], "segment.start"),
            end=_number(data["end"], "segment.end"),
            text=str(data["text"]),
            avg_logprob=(
                None
                if data.get("avg_logprob") is None
                else _number(data["avg_logprob"], "segment.avg_logprob")
            ),
            no_speech_prob=(
                None
                if data.get("no_speech_prob") is None
                else _number(data["no_speech_prob"], "segment.no_speech_prob")
            ),
            words=tuple(WordTiming.from_dict(item) for item in data.get("words", [])),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class RejectedSegment:
    segment: Segment
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"segment": self.segment.to_dict(), "reasons": list(self.reasons)}


@dataclass(frozen=True, slots=True)
class SourceIdentity:
    path: str
    size: int
    modified_ns: int
    fingerprint: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SourceIdentity:
        return cls(
            path=str(data["path"]),
            size=int(data["size"]),
            modified_ns=int(data["modified_ns"]),
            fingerprint=str(data["fingerprint"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class Transcript:
    source: SourceIdentity
    model: str
    device: str
    compute_type: str
    language: str
    created_at: str
    elapsed_seconds: float
    duration_seconds: float | None
    peak_gpu_memory_mib: int | None
    segments: tuple[Segment, ...]
    schema_version: int = SCHEMA_VERSION

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Transcript:
        version = int(data.get("schema_version", 0))
        if version != SCHEMA_VERSION:
            raise ValueError(f"不支持的 transcript schema_version: {version}")
        return cls(
            source=SourceIdentity.from_dict(data["source"]),
            model=str(data["model"]),
            device=str(data["device"]),
            compute_type=str(data["compute_type"]),
            language=str(data["language"]),
            created_at=str(data["created_at"]),
            elapsed_seconds=_number(data["elapsed_seconds"], "elapsed_seconds"),
            duration_seconds=(
                None
                if data.get("duration_seconds") is None
                else _number(data["duration_seconds"], "duration_seconds")
            ),
            peak_gpu_memory_mib=(
                None
                if data.get("peak_gpu_memory_mib") is None
                else int(data["peak_gpu_memory_mib"])
            ),
            segments=tuple(Segment.from_dict(item) for item in data.get("segments", [])),
            schema_version=version,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "source": self.source.to_dict(),
            "segments": [item.to_dict() for item in self.segments],
        }


@dataclass(frozen=True, slots=True)
class FilteredTranscript:
    source: SourceIdentity
    config: dict[str, Any]
    accepted: tuple[Segment, ...]
    rejected: tuple[RejectedSegment, ...]
    schema_version: int = SCHEMA_VERSION

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FilteredTranscript:
        version = int(data.get("schema_version", 0))
        if version != SCHEMA_VERSION:
            raise ValueError(f"不支持的 filtered schema_version: {version}")
        return cls(
            source=SourceIdentity.from_dict(data["source"]),
            config=dict(data.get("config", {})),
            accepted=tuple(Segment.from_dict(item) for item in data.get("accepted", [])),
            rejected=tuple(
                RejectedSegment(
                    segment=Segment.from_dict(item["segment"]),
                    reasons=tuple(str(reason) for reason in item.get("reasons", [])),
                )
                for item in data.get("rejected", [])
            ),
            schema_version=version,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source": self.source.to_dict(),
            "config": self.config,
            "accepted": [item.to_dict() for item in self.accepted],
            "rejected": [item.to_dict() for item in self.rejected],
        }


@dataclass(frozen=True, slots=True)
class TranslationItem:
    id: str
    text: str
    flags: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TranslationItem:
        return cls(
            id=str(data["id"]),
            text=str(data["text"]).strip(),
            flags=tuple(str(flag) for flag in data.get("flags", [])),
        )

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "text": self.text, "flags": list(self.flags)}


@dataclass(frozen=True, slots=True)
class Translation:
    source: SourceIdentity
    model: str
    created_at: str
    batches: tuple[dict[str, Any], ...]
    items: tuple[TranslationItem, ...]
    profile_id: str = ""
    stage: str = "final"
    draft_model: str = ""
    review_model: str | None = None
    prompt_version: str = "context-v4"
    schema_version: int = TRANSLATION_SCHEMA_VERSION

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Translation:
        version = int(data.get("schema_version", 0))
        if version != TRANSLATION_SCHEMA_VERSION:
            raise ValueError(f"不支持的 translation schema_version: {version}")
        return cls(
            source=SourceIdentity.from_dict(data["source"]),
            model=str(data["model"]),
            created_at=str(data["created_at"]),
            batches=tuple(dict(item) for item in data.get("batches", [])),
            items=tuple(TranslationItem.from_dict(item) for item in data.get("items", [])),
            profile_id=str(data.get("profile_id", "")),
            stage=str(data.get("stage", "final")),
            draft_model=str(data.get("draft_model", data.get("model", ""))),
            review_model=(
                None if data.get("review_model") is None else str(data["review_model"])
            ),
            prompt_version=str(data.get("prompt_version", "context-v4")),
            schema_version=version,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source": self.source.to_dict(),
            "model": self.model,
            "created_at": self.created_at,
            "batches": list(self.batches),
            "items": [item.to_dict() for item in self.items],
            "profile_id": self.profile_id,
            "stage": self.stage,
            "draft_model": self.draft_model,
            "review_model": self.review_model,
            "prompt_version": self.prompt_version,
        }


@dataclass(frozen=True, slots=True)
class ScanItem:
    audio: Path
    lrc: Path
    action: str
