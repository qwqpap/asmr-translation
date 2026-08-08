from pathlib import Path

import pytest

from asmr_lrc.scanner import plan_scan, scan_audio


def test_scan_audio_recurses_unicode_paths_and_sorts(tmp_path: Path) -> None:
    paths = [
        tmp_path / "空 格" / "耳かき.FLAC",
        tmp_path / "中文" / "安眠.mp3",
        tmp_path / "a.wav",
    ]
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"audio")
    (tmp_path / "ignore.txt").write_text("x", encoding="utf-8")

    result = scan_audio(tmp_path)

    assert result == sorted(paths, key=lambda path: str(path.relative_to(tmp_path)).casefold())


def test_existing_lrc_is_skipped_unless_overwrite(tmp_path: Path) -> None:
    audio = tmp_path / "音声.m4a"
    audio.write_bytes(b"audio")
    audio.with_suffix(".lrc").write_text("old", encoding="utf-8")

    assert plan_scan(tmp_path, overwrite=False)[0].action == "skip"
    assert plan_scan(tmp_path, overwrite=True)[0].action == "overwrite"


def test_scan_requires_directory(tmp_path: Path) -> None:
    file = tmp_path / "audio.wav"
    file.write_bytes(b"x")
    with pytest.raises(NotADirectoryError):
        scan_audio(file)
