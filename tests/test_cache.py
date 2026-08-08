import json
from pathlib import Path

import pytest

from asmr_lrc.cache import (
    atomic_write_json,
    cache_directory,
    load_json,
    quarantine_corrupt,
    same_source,
    source_identity,
)
from asmr_lrc.errors import CacheError


def test_source_identity_detects_content_change(tmp_path: Path) -> None:
    source = tmp_path / "耳かき.wav"
    source.write_bytes(b"one")
    first = source_identity(source)
    source.write_bytes(b"two")
    second = source_identity(source)

    assert not same_source(first, second)
    assert cache_directory(tmp_path / ".cache", first) != cache_directory(
        tmp_path / ".cache", second
    )


def test_atomic_json_uses_utf8_and_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "缓存" / "result.json"
    atomic_write_json(path, {"text": "日本語和中文"})

    assert load_json(path) == {"text": "日本語和中文"}
    assert json.loads(path.read_text(encoding="utf-8"))["text"] == "日本語和中文"
    assert not list(path.parent.glob("*.tmp"))


def test_corrupt_cache_can_be_quarantined(tmp_path: Path) -> None:
    path = tmp_path / "broken.json"
    path.write_text("{", encoding="utf-8")

    with pytest.raises(CacheError):
        load_json(path)
    target = quarantine_corrupt(path)

    assert not path.exists()
    assert target.name == "broken.json.corrupt-1"
