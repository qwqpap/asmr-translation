from datetime import UTC, datetime

import pytest

from asmr_lrc.models import (
    Segment,
    SourceIdentity,
    Transcript,
    Translation,
    TranslationItem,
    WordTiming,
)


def make_transcript() -> Transcript:
    return Transcript(
        source=SourceIdentity("C:/音声.wav", 12, 34, "abc"),
        model="large-v3",
        device="cuda",
        compute_type="int8_float16",
        language="ja",
        created_at=datetime.now(UTC).isoformat(),
        elapsed_seconds=1.2,
        duration_seconds=2.3,
        peak_gpu_memory_mib=5000,
        segments=(
            Segment(
                "s000001",
                0.2,
                1.1,
                "こんにちは",
                -0.1,
                0.02,
                (WordTiming(0.2, 1.1, "こんにちは", 0.9),),
            ),
        ),
    )


def test_transcript_round_trip() -> None:
    transcript = make_transcript()
    assert Transcript.from_dict(transcript.to_dict()) == transcript


def test_transcript_rejects_unknown_schema() -> None:
    data = make_transcript().to_dict()
    data["schema_version"] = 99
    with pytest.raises(ValueError, match="schema_version"):
        Transcript.from_dict(data)


def test_segment_rejects_invalid_time_range() -> None:
    with pytest.raises(ValueError, match="时间范围"):
        Segment("s1", 2.0, 1.0, "x")


def test_translation_v2_round_trip_preserves_flags() -> None:
    source = SourceIdentity("C:/音声.wav", 12, 34, "abc")
    translation = Translation(
        source=source,
        model="review-model",
        created_at=datetime.now(UTC).isoformat(),
        batches=({"stage": "review"},),
        items=(TranslationItem("s1", "你好", ("review_changed",)),),
        profile_id="profile",
        stage="final",
        draft_model="draft-model",
        review_model="review-model",
    )
    assert Translation.from_dict(translation.to_dict()) == translation


def test_translation_rejects_legacy_schema_without_invalidating_transcript() -> None:
    transcript = make_transcript()
    assert Transcript.from_dict(transcript.to_dict()) == transcript
    legacy = {
        "schema_version": 1,
        "source": transcript.source.to_dict(),
        "model": "old",
        "created_at": transcript.created_at,
        "batches": [],
        "items": [],
    }
    with pytest.raises(ValueError, match="translation schema_version"):
        Translation.from_dict(legacy)
