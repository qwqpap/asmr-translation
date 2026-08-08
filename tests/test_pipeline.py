from datetime import UTC, datetime
from pathlib import Path

from asmr_lrc.cache import atomic_write_json, source_identity
from asmr_lrc.config import AppConfig
from asmr_lrc.errors import AsmrLrcError
from asmr_lrc.models import Segment, Transcript, TranslationItem
from asmr_lrc.pipeline import run_pipeline


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    audio = tmp_path / "日本語 空格.wav"
    audio.write_bytes(b"audio")
    cache = tmp_path / "cache"

    report = run_pipeline(tmp_path, AppConfig(cache_root=cache), dry_run=True)

    assert report.total == 1
    assert not cache.exists()
    assert not audio.with_suffix(".lrc").exists()


def test_pipeline_continues_after_single_file_failure(tmp_path: Path, monkeypatch) -> None:
    good = tmp_path / "good.wav"
    bad = tmp_path / "bad.wav"
    good.write_bytes(b"good")
    bad.write_bytes(b"bad")
    cache = tmp_path / "cache"

    def fake_asr(audio: Path, output: Path, _log: Path, **_kwargs: object) -> None:
        if audio.name == "bad.wav":
            raise OSError("损坏音频")
        transcript = Transcript(
            source=source_identity(audio),
            model="large-v3",
            device="cuda",
            compute_type="int8_float16",
            language="ja",
            created_at=datetime.now(UTC).isoformat(),
            elapsed_seconds=1,
            duration_seconds=2,
            peak_gpu_memory_mib=1,
            segments=(Segment("s000001", 0, 1, "こんばんは。"),),
        )
        atomic_write_json(output, transcript.to_dict())

    monkeypatch.setattr("asmr_lrc.pipeline.run_asr_process", fake_asr)
    monkeypatch.setattr(
        "asmr_lrc.pipeline.translate_segments",
        lambda segments, **_kwargs: (
            tuple(TranslationItem(item.id, "晚安。") for item in segments),
            ({"validation": "ok"},),
        ),
    )
    monkeypatch.setattr("asmr_lrc.pipeline.unload_model", lambda *_args: None)

    report = run_pipeline(tmp_path, AppConfig(cache_root=cache))

    assert report.succeeded == 1
    assert report.failed == 1
    assert good.with_suffix(".lrc").read_text(encoding="utf-8") == "[00:00.00]晚安。\n"
    assert not bad.with_suffix(".lrc").exists()
    assert report.exit_code == 1


def test_pipeline_resumes_completed_translation_batches(tmp_path: Path, monkeypatch) -> None:
    audio = tmp_path / "resume.wav"
    audio.write_bytes(b"resume")
    cache = tmp_path / "cache"
    calls: list[tuple[str, ...]] = []

    def fake_asr(audio_path: Path, output: Path, _log: Path, **_kwargs: object) -> None:
        transcript = Transcript(
            source=source_identity(audio_path),
            model="large-v3",
            device="cuda",
            compute_type="int8_float16",
            language="ja",
            created_at=datetime.now(UTC).isoformat(),
            elapsed_seconds=1,
            duration_seconds=6,
            peak_gpu_memory_mib=1,
            segments=(
                Segment("s1", 0, 1, "一。"),
                Segment("s2", 2, 3, "二。"),
                Segment("s3", 4, 5, "三。"),
            ),
        )
        atomic_write_json(output, transcript.to_dict())

    def interrupting_translate(segments, **_kwargs):
        calls.append(tuple(segment.id for segment in segments))
        if len(calls) == 2:
            raise AsmrLrcError("模拟中断")
        return tuple(TranslationItem(segment.id, "译文") for segment in segments), (
            {"validation": "ok"},
        )

    monkeypatch.setattr("asmr_lrc.pipeline.run_asr_process", fake_asr)
    monkeypatch.setattr("asmr_lrc.pipeline.translate_segments", interrupting_translate)
    monkeypatch.setattr("asmr_lrc.pipeline.unload_model", lambda *_args: None)
    first = run_pipeline(tmp_path, AppConfig(cache_root=cache, translation_batch_size=2))

    assert first.failed == 1
    assert calls == [("s1", "s2"), ("s3",)]
    assert list(cache.rglob("translation.zh-CN.partial.json"))

    resumed_calls: list[tuple[str, ...]] = []

    def resumed_translate(segments, **_kwargs):
        resumed_calls.append(tuple(segment.id for segment in segments))
        return tuple(TranslationItem(segment.id, "译文") for segment in segments), (
            {"validation": "ok"},
        )

    monkeypatch.setattr("asmr_lrc.pipeline.translate_segments", resumed_translate)
    second = run_pipeline(tmp_path, AppConfig(cache_root=cache, translation_batch_size=2))

    assert second.succeeded == 1
    assert resumed_calls == [("s3",)]
    assert audio.with_suffix(".lrc").exists()
    assert not list(cache.rglob("translation.zh-CN.partial.json"))
