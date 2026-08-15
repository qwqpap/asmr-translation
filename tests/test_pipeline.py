from datetime import UTC, datetime
from pathlib import Path

import pytest

from asmr_lrc.cache import atomic_write_json, source_identity
from asmr_lrc.config import AppConfig
from asmr_lrc.control import CancelledError, CancelToken
from asmr_lrc.errors import AsmrLrcError, TranslationError
from asmr_lrc.models import Segment, Transcript, TranslationItem
from asmr_lrc.pipeline import _provider_pair, _translate_with_fallback, run_pipeline
from asmr_lrc.providers import ProviderConfig, ProviderResponse
from asmr_lrc.translation_context import ContextMemory


class FakeProvider:
    config = ProviderConfig("ollama", "http://local", "model")

    def check(self) -> None:
        return

    def generate(self, **_kwargs) -> ProviderResponse:
        raise AssertionError("测试应替换 translate_contextual_batch")

    def unload(self) -> None:
        return


def pipeline_config(cache: Path, *, batch_size: int = 12) -> AppConfig:
    provider = ProviderConfig("ollama", "http://local", "model")
    return AppConfig(
        cache_root=cache,
        quality_mode="balanced",
        review_enabled=False,
        translation_batch_size=batch_size,
        draft_provider=provider,
        review_provider=None,
    )


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
    monkeypatch.setattr("asmr_lrc.pipeline._provider_pair", lambda _config: (FakeProvider(), None))
    monkeypatch.setattr(
        "asmr_lrc.pipeline.translate_contextual_batch",
        lambda segments, indices, **_kwargs: (
            tuple(TranslationItem(segments[index].id, "晚安。") for index in indices),
            {"validation": "ok"},
        ),
    )

    report = run_pipeline(tmp_path, pipeline_config(cache))

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

    def interrupting_translate(segments, indices, **_kwargs):
        calls.append(tuple(segments[index].id for index in indices))
        if len(calls) == 2:
            raise AsmrLrcError("模拟中断")
        return tuple(TranslationItem(segments[index].id, "译文") for index in indices), (
            {"validation": "ok"}
        )

    monkeypatch.setattr("asmr_lrc.pipeline.run_asr_process", fake_asr)
    monkeypatch.setattr("asmr_lrc.pipeline._provider_pair", lambda _config: (FakeProvider(), None))
    monkeypatch.setattr("asmr_lrc.pipeline.translate_contextual_batch", interrupting_translate)
    config = pipeline_config(cache, batch_size=2)
    first = run_pipeline(tmp_path, config)

    assert first.failed == 1
    assert calls == [("s1", "s2"), ("s3",)]
    assert list(cache.rglob("translation.zh-CN.partial.json"))

    resumed_calls: list[tuple[str, ...]] = []

    def resumed_translate(segments, indices, **_kwargs):
        resumed_calls.append(tuple(segments[index].id for index in indices))
        return tuple(TranslationItem(segments[index].id, "译文") for index in indices), (
            {"validation": "ok"}
        )

    monkeypatch.setattr("asmr_lrc.pipeline.translate_contextual_batch", resumed_translate)
    second = run_pipeline(tmp_path, config)

    assert second.succeeded == 1
    assert resumed_calls == [("s3",)]
    assert audio.with_suffix(".lrc").exists()
    assert not list(cache.rglob("translation.zh-CN.partial.json"))


def test_pipeline_honors_pre_cancelled_token_without_writes(tmp_path: Path) -> None:
    (tmp_path / "cancel.wav").write_bytes(b"audio")
    cache = tmp_path / "cache"
    token = CancelToken()
    token.cancel()

    with pytest.raises(CancelledError):
        run_pipeline(tmp_path, pipeline_config(cache), cancel_token=token)

    assert not cache.exists()


def test_provider_pair_keeps_independent_stage_credentials(tmp_path: Path) -> None:
    draft = ProviderConfig(
        "openai", "https://example.test/v1", "same-model", api_key="draft-key"
    )
    review = ProviderConfig(
        "openai", "https://example.test/v1", "same-model", api_key="review-key"
    )
    draft_runtime, review_runtime = _provider_pair(
        AppConfig(cache_root=tmp_path, draft_provider=draft, review_provider=review)
    )

    assert review_runtime is not None
    assert review_runtime is not draft_runtime
    assert draft_runtime.config.api_key == "draft-key"
    assert review_runtime.config.api_key == "review-key"


def test_primary_translation_failure_unloads_before_qwen_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class StageProvider(FakeProvider):
        def __init__(self, name: str) -> None:
            self.name = name
            self.config = ProviderConfig("ollama", "http://local", name)

        def check(self) -> None:
            events.append(f"{self.name}.check")

        def unload(self) -> None:
            events.append(f"{self.name}.unload")

    primary = StageProvider("translategemma:4b")
    fallback = StageProvider("qwen3.5-9b-abliterated:latest")
    calls = 0

    def translate(segments, indices, *, provider, **_kwargs):
        nonlocal calls
        calls += 1
        if provider is primary:
            raise TranslationError("严格 JSON 校验失败")
        return tuple(TranslationItem(segments[index].id, "兜底译文") for index in indices), {
            "validation": "ok"
        }

    monkeypatch.setattr("asmr_lrc.pipeline.translate_contextual_batch", translate)
    items, metrics = _translate_with_fallback(
        (Segment("s1", 0, 1, "こんにちは"),),
        (0,),
        provider=primary,
        fallback=fallback,
        memory=ContextMemory(),
        context_before=0,
        context_after=0,
        retries=0,
    )

    assert items[0].text == "兜底译文"
    assert metrics["fallback"] is True
    assert calls == 2
    assert events == [
        "translategemma:4b.unload",
        "qwen3.5-9b-abliterated:latest.check",
        "qwen3.5-9b-abliterated:latest.unload",
    ]


def test_reloaded_primary_is_unloaded_after_a_later_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class StageProvider(FakeProvider):
        def __init__(self, name: str) -> None:
            self.name = name
            self.config = ProviderConfig("ollama", "http://local", name)

        def check(self) -> None:
            events.append(f"{self.name}.check")

        def unload(self) -> None:
            events.append(f"{self.name}.unload")

    primary = StageProvider("translategemma:4b")
    fallback = StageProvider("qwen3.5-9b-abliterated:latest")
    attempts = 0

    def translate(segments, indices, *, provider, **_kwargs):
        nonlocal attempts
        attempts += 1
        if provider is primary and attempts == 1:
            raise TranslationError("第一批结构失败")
        return tuple(TranslationItem(segments[index].id, "译文") for index in indices), {
            "validation": "ok"
        }

    monkeypatch.setattr("asmr_lrc.pipeline.translate_contextual_batch", translate)
    unloaded: set[int] = set()
    segment = Segment("s1", 0, 1, "こんにちは")
    kwargs = {
        "fallback": fallback,
        "memory": ContextMemory(),
        "context_before": 0,
        "context_after": 0,
        "retries": 0,
        "unloaded_provider_ids": unloaded,
    }
    _translate_with_fallback((segment,), (0,), provider=primary, **kwargs)
    _translate_with_fallback((segment,), (0,), provider=primary, **kwargs)

    assert events == [
        "translategemma:4b.unload",
        "qwen3.5-9b-abliterated:latest.check",
        "qwen3.5-9b-abliterated:latest.unload",
    ]
    # A caller that reloads the primary for another batch must clear the mark;
    # the helper does so before the second call above.
    assert id(primary) not in unloaded


def test_quality_pipeline_completes_all_context_before_translation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audio_paths = [tmp_path / "one.wav", tmp_path / "two.wav"]
    for audio in audio_paths:
        audio.write_bytes(b"audio")
    events: list[str] = []

    def fake_asr(audio: Path, output: Path, _log: Path, **_kwargs: object) -> None:
        events.append(f"asr:{audio.stem}")
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
            segments=(Segment("s1", 0, 1, "こんばんは。"),),
        )
        atomic_write_json(output, transcript.to_dict())

    class StageProvider(FakeProvider):
        def __init__(self, name: str) -> None:
            self.config = ProviderConfig("ollama", "http://local", name)

        def check(self) -> None:
            return

        def unload(self) -> None:
            events.append(f"unload:{self.config.model}")

    draft = StageProvider("translategemma:4b")
    analysis = StageProvider("qwen3.5-9b-abliterated:latest")
    draft_config = ProviderConfig("ollama", "http://local", "translategemma:4b")
    analysis_config = ProviderConfig(
        "ollama", "http://local", "qwen3.5-9b-abliterated:latest"
    )
    config = AppConfig(
        cache_root=tmp_path / "cache",
        quality_mode="quality",
        review_enabled=False,
        draft_provider=draft_config,
        analysis_provider=analysis_config,
        fallback_provider=analysis_config,
    )

    def fake_analyze(segments, **_kwargs):
        events.append(f"analyze:{segments[0].id}")
        return ContextMemory(), ()

    def fake_translate(segments, indices, *, provider, **_kwargs):
        assert "unload:qwen3.5-9b-abliterated:latest" in events
        events.append(f"translate:{segments[indices[0]].id}")
        return tuple(TranslationItem(segments[index].id, "晚安") for index in indices), {
            "validation": "ok"
        }

    monkeypatch.setattr("asmr_lrc.pipeline.run_asr_process", fake_asr)
    monkeypatch.setattr("asmr_lrc.pipeline._provider_pair", lambda _config: (draft, None))
    monkeypatch.setattr(
        "asmr_lrc.pipeline._analysis_and_fallback_providers",
        lambda _config, _draft, _review: (analysis, analysis),
    )
    monkeypatch.setattr("asmr_lrc.pipeline.analyze_context", fake_analyze)
    monkeypatch.setattr("asmr_lrc.pipeline.translate_contextual_batch", fake_translate)

    report = run_pipeline(tmp_path, config, quiet=True)

    assert report.succeeded == 2
    assert events.index("unload:qwen3.5-9b-abliterated:latest") < events.index(
        "translate:s1"
    )
    assert events[-1] == "unload:translategemma:4b"
