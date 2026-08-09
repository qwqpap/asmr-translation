from __future__ import annotations

import argparse
import difflib
import json
import re
from pathlib import Path

_VTT_TIME = re.compile(
    r"^(?P<hour>\d{2}):(?P<minute>\d{2}):(?P<second>\d{2})\.(?P<millis>\d{3})$"
)
_LRC_LINE = re.compile(r"^\[(?P<minute>\d+):(?P<second>\d{2})\.(?P<centi>\d{2})](?P<text>.*)$")


def vtt_seconds(value: str) -> float:
    match = _VTT_TIME.fullmatch(value)
    if match is None:
        raise ValueError(f"VTT 时间无效: {value}")
    return (
        int(match["hour"]) * 3600
        + int(match["minute"]) * 60
        + int(match["second"])
        + int(match["millis"]) / 1000
    )


def parse_vtt(path: Path) -> list[tuple[float, float, str]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    result: list[tuple[float, float, str]] = []
    for index, line in enumerate(lines):
        if " --> " not in line:
            continue
        start_text, end_text = line.split(" --> ", 1)
        if index + 1 >= len(lines):
            continue
        text = lines[index + 1].strip()
        if text:
            result.append((vtt_seconds(start_text), vtt_seconds(end_text), text))
    return result


def parse_lrc(path: Path) -> list[tuple[float, str]]:
    result: list[tuple[float, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        match = _LRC_LINE.fullmatch(line)
        if match is None:
            continue
        seconds = int(match["minute"]) * 60 + int(match["second"]) + int(match["centi"]) / 100
        result.append((seconds, match["text"].strip()))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="将生成 LRC 与参考 VTT 做时间/文本对照")
    parser.add_argument("lrc", type=Path)
    parser.add_argument("reference", type=Path)
    parser.add_argument("--max-time-error", type=float, default=1.5)
    args = parser.parse_args()
    reference = parse_vtt(args.reference)
    generated = parse_lrc(args.lrc)
    pairs: list[dict[str, object]] = []
    for start, text in generated:
        if not reference:
            continue
        match = min(reference, key=lambda cue: abs(cue[0] - start))
        time_error = abs(match[0] - start)
        similarity = difflib.SequenceMatcher(None, text, match[2]).ratio()
        pairs.append(
            {
                "generated_start": start,
                "reference_start": match[0],
                "time_error": round(time_error, 3),
                "similarity": round(similarity, 3),
                "generated": text,
                "reference": match[2],
            }
        )
    errors = [pair["time_error"] for pair in pairs]
    similarities = [pair["similarity"] for pair in pairs]
    suspicious = [
        pair
        for pair in pairs
        if pair["time_error"] > args.max_time_error or pair["similarity"] < 0.45
    ]
    result = {
        "generated_lines": len(generated),
        "reference_cues": len(reference),
        "paired": len(pairs),
        "timestamp": {
            "mean_absolute_error": round(sum(errors) / len(errors), 3) if errors else None,
            "over_threshold": sum(error > args.max_time_error for error in errors),
        },
        "text_similarity": {
            "mean_sequence_ratio": round(sum(similarities) / len(similarities), 3)
            if similarities
            else None,
            "below_0_45": sum(value < 0.45 for value in similarities),
        },
        "suspicious_examples": suspicious[:20],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

