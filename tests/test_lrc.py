from pathlib import Path

import pytest

from asmr_lrc.errors import LrcError
from asmr_lrc.lrc import format_timestamp, render_lrc, validate_lrc, write_lrc_atomic
from asmr_lrc.models import Segment, TranslationItem


def test_timestamp_rounding_and_long_minutes() -> None:
    assert format_timestamp(3.245) == "[00:03.25]"
    assert format_timestamp(3600.01) == "[60:00.01]"


def test_render_pure_chinese_lrc() -> None:
    content = render_lrc(
        (Segment("s1", 3.25, 5, "お疲れ様"), Segment("s2", 8.7, 10, "始めます")),
        (TranslationItem("s1", "今天辛苦了。"), TranslationItem("s2", "开始吧。")),
    )
    assert content == "[00:03.25]今天辛苦了。\n[00:08.70]开始吧。\n"
    validate_lrc(content)


def test_atomic_write_preserves_existing_without_overwrite(tmp_path: Path) -> None:
    path = tmp_path / "中文 空格 耳かき.lrc"
    path.write_text("old", encoding="utf-8")
    with pytest.raises(FileExistsError):
        write_lrc_atomic(path, "[00:00.00]新内容\n", overwrite=False)
    assert path.read_text(encoding="utf-8") == "old"


def test_atomic_write_unicode_long_name(tmp_path: Path) -> None:
    path = tmp_path / (("很长的名字" * 10) + " 耳かき.lrc")
    write_lrc_atomic(path, "[00:00.00]晚安\n", overwrite=False)
    assert path.read_text(encoding="utf-8") == "[00:00.00]晚安\n"


def test_validate_rejects_empty_duplicate_and_backwards_lines() -> None:
    with pytest.raises(LrcError):
        validate_lrc("[00:01.00]a\n\n")
    with pytest.raises(LrcError):
        validate_lrc("[00:01.00]a\n[00:01.00]a\n")
    with pytest.raises(LrcError):
        validate_lrc("[00:02.00]a\n[00:01.00]b\n")
