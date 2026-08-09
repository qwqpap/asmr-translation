from dataclasses import replace

from asmr_lrc.config import FilterConfig
from asmr_lrc.filters import filter_transcript
from asmr_lrc.models import Segment, SourceIdentity, Transcript, WordTiming


def transcript(*segments: Segment) -> Transcript:
    return Transcript(
        source=SourceIdentity("C:/a.wav", 1, 2, "abc"),
        model="large-v3",
        device="cuda",
        compute_type="int8_float16",
        language="ja",
        created_at="2026-01-01T00:00:00+00:00",
        elapsed_seconds=1,
        duration_seconds=30,
        peak_gpu_memory_mib=None,
        segments=segments,
    )


def test_known_hallucination_is_rejected_with_reason() -> None:
    result = filter_transcript(
        transcript(Segment("s1", 0, 2, "ご視聴ありがとうございました", -0.1, 0.1)),
        FilterConfig(),
    )
    assert not result.accepted
    assert result.rejected[0].reasons == ("known_hallucination_phrase",)


def test_high_no_speech_rejected_but_low_confidence_whisper_preserved() -> None:
    noisy = Segment("s1", 0, 2, "謎の音", -2.0, 0.99)
    whisper = Segment("s2", 2.5, 3.5, "だいじょうぶ", -2.5, 0.4)
    result = filter_transcript(transcript(noisy, whisper), FilterConfig())

    assert [item.id for item in result.accepted] == ["s2"]
    assert "high_no_speech_probability" in result.rejected[0].reasons


def test_only_excess_repetitions_are_rejected() -> None:
    segments = tuple(Segment(f"s{i}", i, i + 0.5, "同じ") for i in range(4))
    result = filter_transcript(
        transcript(*segments),
        replace(FilterConfig(), merge_gap_seconds=0, repeated_text_limit=2),
    )
    assert [item.id for item in result.accepted] == ["s0", "s1"]
    assert all("excessive_repetition" in item.reasons for item in result.rejected)


def test_long_line_splits_on_real_word_timestamps() -> None:
    words = tuple(
        WordTiming(index * 0.5, (index + 1) * 0.5, word)
        for index, word in enumerate(("これは", "とても", "長い", "文章です。"))
    )
    segment = Segment("s1", 0, 2, "これはとても長い文章です。", words=words)
    result = filter_transcript(transcript(segment), replace(FilterConfig(), max_line_characters=7))

    assert [item.id for item in result.accepted] == ["s1.1", "s1.2"]
    assert result.accepted[0].start == 0
    assert result.accepted[0].end == 1.0
    assert result.accepted[1].start == 1.0


def test_adjacent_fragments_merge_without_inventing_times() -> None:
    result = filter_transcript(
        transcript(
            Segment("s1", 1, 2, "今日は"),
            Segment("s2", 2.1, 3, "大丈夫"),
        ),
        FilterConfig(),
    )
    assert len(result.accepted) == 1
    assert (result.accepted[0].start, result.accepted[0].end) == (1, 3)
