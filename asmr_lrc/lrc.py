from __future__ import annotations

import os
import re
import tempfile
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from .errors import LrcError
from .models import Segment, TranslationItem

_TIMESTAMP = re.compile(r"^\[(\d{2,}):(\d{2})\.(\d{2})](.*)$")


def format_timestamp(seconds: float) -> str:
    if seconds < 0:
        raise LrcError("时间戳不能为负数")
    centiseconds = int((Decimal(str(seconds)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    minutes, remainder = divmod(centiseconds, 6000)
    secs, fraction = divmod(remainder, 100)
    return f"[{minutes:02d}:{secs:02d}.{fraction:02d}]"


def render_lrc(segments: tuple[Segment, ...], translations: tuple[TranslationItem, ...]) -> str:
    by_id = {item.id: item.text.strip() for item in translations}
    if len(by_id) != len(translations):
        raise LrcError("译文 ID 重复")
    segment_ids = [segment.id for segment in segments]
    if set(by_id) != set(segment_ids):
        missing = sorted(set(segment_ids) - set(by_id))
        extra = sorted(set(by_id) - set(segment_ids))
        raise LrcError(f"译文 ID 不匹配，缺少={missing}，额外={extra}")
    lines: list[str] = []
    previous = -1.0
    for segment in sorted(segments, key=lambda item: (item.start, item.end, item.id)):
        if segment.start < previous:
            raise LrcError("字幕时间戳必须单调递增")
        text = by_id[segment.id]
        if text:
            clean = " ".join(text.splitlines()).strip()
            if clean:
                lines.append(f"{format_timestamp(segment.start)}{clean}")
        previous = segment.start
    return "\n".join(lines) + ("\n" if lines else "")


def validate_lrc(content: str) -> None:
    previous = -1
    seen: set[tuple[int, str]] = set()
    for line_number, line in enumerate(content.splitlines(), 1):
        if not line.strip():
            raise LrcError(f"LRC 第 {line_number} 行为空")
        match = _TIMESTAMP.fullmatch(line)
        if match is None:
            raise LrcError(f"LRC 第 {line_number} 行格式无效")
        minutes, seconds, fraction, text = match.groups()
        if int(seconds) >= 60:
            raise LrcError(f"LRC 第 {line_number} 行秒数无效")
        timestamp = int(minutes) * 6000 + int(seconds) * 100 + int(fraction)
        if timestamp < previous:
            raise LrcError(f"LRC 第 {line_number} 行时间倒退")
        if not text.strip():
            raise LrcError(f"LRC 第 {line_number} 行文本为空")
        key = (timestamp, text.strip())
        if key in seen:
            raise LrcError(f"LRC 第 {line_number} 行重复")
        seen.add(key)
        previous = timestamp


def write_lrc_atomic(path: Path, content: str, *, overwrite: bool) -> None:
    validate_lrc(content)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not overwrite:
        raise FileExistsError(f"LRC 已存在: {path}")
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if path.exists() and not overwrite:
            raise FileExistsError(f"LRC 已存在: {path}")
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
