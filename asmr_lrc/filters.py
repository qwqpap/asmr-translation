from __future__ import annotations

import re
from dataclasses import asdict, replace

from .config import FilterConfig
from .models import FilteredTranscript, RejectedSegment, Segment, Transcript, WordTiming

_SPACE = re.compile(r"\s+")
_TRAILING_PUNCTUATION = frozenset("。！？!?…")


def normalize_text(text: str) -> str:
    return _SPACE.sub("", text).strip()


def _split_words(segment: Segment, max_characters: int) -> list[Segment]:
    if len(normalize_text(segment.text)) <= max_characters or not segment.words:
        return [segment]
    chunks: list[list[WordTiming]] = []
    current: list[WordTiming] = []
    length = 0
    for word in segment.words:
        word_length = len(normalize_text(word.word))
        if current and length + word_length > max_characters:
            chunks.append(current)
            current = []
            length = 0
        current.append(word)
        length += word_length
        if length >= max_characters and word.word.rstrip().endswith(tuple(_TRAILING_PUNCTUATION)):
            chunks.append(current)
            current = []
            length = 0
    if current:
        chunks.append(current)
    if len(chunks) == 1:
        return [segment]
    return [
        replace(
            segment,
            id=f"{segment.id}.{index + 1}",
            start=chunk[0].start,
            end=chunk[-1].end,
            text="".join(word.word for word in chunk).strip(),
            words=tuple(chunk),
        )
        for index, chunk in enumerate(chunks)
    ]


def _merge_adjacent(segments: list[Segment], config: FilterConfig) -> list[Segment]:
    merged: list[Segment] = []
    for segment in segments:
        if not merged:
            merged.append(segment)
            continue
        previous = merged[-1]
        combined_length = len(normalize_text(previous.text + segment.text))
        can_merge = (
            segment.start - previous.end <= config.merge_gap_seconds
            and combined_length <= config.max_line_characters
            and not previous.text.rstrip().endswith(tuple(_TRAILING_PUNCTUATION))
        )
        if can_merge:
            merged[-1] = Segment(
                id=f"{previous.id}+{segment.id}",
                start=previous.start,
                end=segment.end,
                text=f"{previous.text.rstrip()}{segment.text.lstrip()}",
                avg_logprob=(
                    None
                    if previous.avg_logprob is None or segment.avg_logprob is None
                    else (previous.avg_logprob + segment.avg_logprob) / 2
                ),
                no_speech_prob=max(
                    value
                    for value in (previous.no_speech_prob, segment.no_speech_prob)
                    if value is not None
                )
                if previous.no_speech_prob is not None or segment.no_speech_prob is not None
                else None,
                words=previous.words + segment.words,
            )
        else:
            merged.append(segment)
    return merged


def filter_transcript(transcript: Transcript, config: FilterConfig) -> FilteredTranscript:
    accepted: list[Segment] = []
    rejected: list[RejectedSegment] = []
    previous_normalized = ""
    repetition_count = 0
    previous_end = 0.0

    for segment in sorted(transcript.segments, key=lambda item: (item.start, item.end, item.id)):
        text = normalize_text(segment.text)
        reasons: list[str] = []
        if not text:
            reasons.append("empty_text")
        if any(phrase in text for phrase in config.hallucination_phrases):
            reasons.append("known_hallucination_phrase")
        duration = segment.end - segment.start
        high_no_speech = (
            segment.no_speech_prob is not None
            and segment.no_speech_prob >= config.high_no_speech_probability
        )
        if high_no_speech and duration >= config.minimum_non_speech_duration:
            reasons.append("high_no_speech_probability")
        if high_no_speech and segment.start - previous_end >= config.long_silence_seconds and text:
            reasons.append("hallucination_after_long_silence")

        if text and text == previous_normalized:
            repetition_count += 1
        else:
            previous_normalized = text
            repetition_count = 1
        if repetition_count > config.repeated_text_limit:
            reasons.append("excessive_repetition")

        if reasons:
            rejected.append(RejectedSegment(segment=segment, reasons=tuple(dict.fromkeys(reasons))))
        else:
            accepted.extend(_split_words(segment, config.max_line_characters))
        previous_end = max(previous_end, segment.end)

    accepted = _merge_adjacent(accepted, config)
    for earlier, later in zip(accepted, accepted[1:], strict=False):
        if later.start < earlier.start:
            raise ValueError("过滤后的时间戳不是单调递增")
    return FilteredTranscript(
        source=transcript.source,
        config=asdict(config),
        accepted=tuple(accepted),
        rejected=tuple(rejected),
    )
