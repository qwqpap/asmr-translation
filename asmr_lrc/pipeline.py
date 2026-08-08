from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from .asr import run_asr_process
from .cache import (
    atomic_write_json,
    cache_directory,
    load_validated,
    quarantine_corrupt,
    same_source,
    source_identity,
)
from .config import AppConfig
from .errors import AsmrLrcError, CacheError
from .filters import filter_transcript
from .lrc import render_lrc, write_lrc_atomic
from .models import FilteredTranscript, SourceIdentity, Transcript, Translation
from .reporting import BatchReport
from .scanner import plan_scan
from .translator import translate_segments, unload_model


def _log(path: Path, message: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).isoformat()
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(f"{stamp} {message}\n")


def _load_or_none(path: Path, parser: object) -> object | None:
    if not path.exists():
        return None
    try:
        return load_validated(path, parser)  # type: ignore[arg-type]
    except CacheError as exc:
        quarantined = quarantine_corrupt(path)
        raise CacheError(f"缓存损坏，已隔离为 {quarantined.name}") from exc


def _transcript_valid(transcript: Transcript, source: SourceIdentity, config: AppConfig) -> bool:
    return (
        same_source(transcript.source, source)
        and transcript.model in {config.asr_model, config.fallback_asr_model}
        and transcript.device == config.device
        and transcript.compute_type == config.compute_type
        and transcript.language == "ja"
    )


def _filtered_valid(
    filtered: FilteredTranscript,
    transcript: Transcript,
    config: AppConfig,
) -> bool:
    return same_source(filtered.source, transcript.source) and filtered.config == asdict(
        config.filter
    )


def _translation_valid(
    translation: Translation,
    filtered: FilteredTranscript,
    config: AppConfig,
) -> bool:
    expected = tuple(item.id for item in filtered.accepted)
    actual = tuple(item.id for item in translation.items)
    return (
        same_source(translation.source, filtered.source)
        and translation.model == config.ollama_model
        and actual == expected
        and all(item.text.strip() for item in translation.items)
    )


def _translation_partial_valid(
    translation: Translation,
    filtered: FilteredTranscript,
    config: AppConfig,
) -> bool:
    expected = {item.id for item in filtered.accepted}
    actual = [item.id for item in translation.items]
    return (
        same_source(translation.source, filtered.source)
        and translation.model == config.ollama_model
        and len(actual) == len(set(actual))
        and set(actual).issubset(expected)
        and all(item.text.strip() for item in translation.items)
    )


def _write_source_metadata(path: Path, source: SourceIdentity) -> None:
    atomic_write_json(
        path,
        {
            "schema_version": 1,
            "source": source.to_dict(),
            "tool_version": "0.1.0",
            "updated_at": datetime.now(UTC).isoformat(),
        },
    )


def run_pipeline(
    root: Path,
    config: AppConfig,
    *,
    dry_run: bool = False,
    transcribe_only: bool = False,
    translate_only: bool = False,
    release_ollama: bool = True,
) -> BatchReport:
    plan = plan_scan(root, overwrite=config.overwrite)
    report = BatchReport(total=len(plan))
    for item in plan:
        print(f"[{item.action}] {item.audio}")
        if item.action == "skip":
            report.skipped += 1
    if dry_run:
        return report

    work = [item for item in plan if item.action != "skip"]
    contexts: dict[Path, tuple[SourceIdentity, Path, Transcript, FilteredTranscript]] = {}

    # Phase 1: all ASR subprocesses finish before Ollama translation starts.
    for index, item in enumerate(work, 1):
        print(f"ASR {index}/{len(work)}: {item.audio}", flush=True)
        try:
            source = source_identity(item.audio)
            directory = cache_directory(config.cache_root, source)
            raw_path = directory / "transcript.raw.json"
            filtered_path = directory / "transcript.filtered.json"
            log_path = directory / "process.log"
            _write_source_metadata(directory / "source.json", source)

            transcript: Transcript | None = None
            try:
                cached = _load_or_none(raw_path, Transcript.from_dict)
                if isinstance(cached, Transcript) and _transcript_valid(cached, source, config):
                    transcript = cached
                    report.cache_hits += 1
                    _log(log_path, "transcript cache hit")
            except CacheError as exc:
                _log(log_path, str(exc))
                if translate_only:
                    raise

            if transcript is None:
                if translate_only:
                    raise CacheError(f"--translate-only 需要有效转写缓存: {raw_path}")
                models = [config.asr_model]
                if config.fallback_asr_model and config.fallback_asr_model != config.asr_model:
                    models.append(config.fallback_asr_model)
                last_error: AsmrLrcError | None = None
                for model in models:
                    try:
                        run_asr_process(
                            item.audio,
                            raw_path,
                            log_path,
                            model=model,
                            device=config.device,
                            compute_type=config.compute_type,
                            ollama_url=config.ollama_url,
                        )
                        transcript = load_validated(raw_path, Transcript.from_dict)
                        report.transcribed += 1
                        break
                    except AsmrLrcError as exc:
                        last_error = exc
                        _log(log_path, f"ASR failure model={model}: {exc}")
                if transcript is None:
                    raise last_error or AsmrLrcError("ASR 未生成结果")

            filtered: FilteredTranscript | None = None
            try:
                cached_filtered = _load_or_none(filtered_path, FilteredTranscript.from_dict)
                if isinstance(cached_filtered, FilteredTranscript) and _filtered_valid(
                    cached_filtered, transcript, config
                ):
                    filtered = cached_filtered
                    report.cache_hits += 1
            except CacheError as exc:
                _log(log_path, str(exc))
            if filtered is None:
                filtered = filter_transcript(transcript, config.filter)
                atomic_write_json(filtered_path, filtered.to_dict())
            contexts[item.audio] = (source, directory, transcript, filtered)
            if transcribe_only:
                report.succeeded += 1
        except (OSError, ValueError, AsmrLrcError) as exc:
            report.add_failure(str(item.audio), str(exc))
            print(f"失败: {item.audio}: {exc}", flush=True)

    if transcribe_only:
        return report

    # Phase 2: Whisper processes have exited; only now may Ollama load a model.
    try:
        for index, item in enumerate(work, 1):
            context = contexts.get(item.audio)
            if context is None:
                continue
            source, directory, _transcript, filtered = context
            translation_path = directory / "translation.zh-CN.json"
            partial_path = directory / "translation.zh-CN.partial.json"
            log_path = directory / "process.log"
            print(f"翻译 {index}/{len(work)}: {item.audio}", flush=True)
            try:
                translation: Translation | None = None
                try:
                    cached_translation = _load_or_none(translation_path, Translation.from_dict)
                    if isinstance(cached_translation, Translation) and _translation_valid(
                        cached_translation, filtered, config
                    ):
                        translation = cached_translation
                        report.cache_hits += 1
                except CacheError as exc:
                    _log(log_path, str(exc))
                if translation is None:
                    partial: Translation | None = None
                    try:
                        cached_partial = _load_or_none(partial_path, Translation.from_dict)
                        if isinstance(cached_partial, Translation) and _translation_partial_valid(
                            cached_partial, filtered, config
                        ):
                            partial = cached_partial
                            report.cache_hits += 1
                    except CacheError as exc:
                        _log(log_path, str(exc))

                    expected_ids = tuple(segment.id for segment in filtered.accepted)
                    item_by_id = (
                        {} if partial is None else {item.id: item for item in partial.items}
                    )
                    batches = [] if partial is None else list(partial.batches)
                    total_batches = (
                        len(filtered.accepted) + config.translation_batch_size - 1
                    ) // config.translation_batch_size
                    for offset in range(0, len(filtered.accepted), config.translation_batch_size):
                        batch_number = offset // config.translation_batch_size + 1
                        segments = filtered.accepted[
                            offset : offset + config.translation_batch_size
                        ]
                        missing = tuple(
                            segment for segment in segments if segment.id not in item_by_id
                        )
                        if not missing:
                            continue
                        print(
                            f"翻译批次 {batch_number}/{total_batches}: {item.audio.name}",
                            flush=True,
                        )
                        new_items, new_batches = translate_segments(
                            missing,
                            base_url=config.ollama_url,
                            model=config.ollama_model,
                            batch_size=len(missing),
                            retries=config.translation_retries,
                            keep_alive=config.ollama_keep_alive,
                        )
                        item_by_id.update({item.id: item for item in new_items})
                        batches.extend(new_batches)
                        partial = Translation(
                            source=source,
                            model=config.ollama_model,
                            created_at=(
                                partial.created_at
                                if partial is not None
                                else datetime.now(UTC).isoformat()
                            ),
                            batches=tuple(batches),
                            items=tuple(
                                item_by_id[item_id]
                                for item_id in expected_ids
                                if item_id in item_by_id
                            ),
                        )
                        atomic_write_json(partial_path, partial.to_dict())
                    if set(item_by_id) != set(expected_ids):
                        missing_ids = sorted(set(expected_ids) - set(item_by_id))
                        raise AsmrLrcError(f"翻译未完成，缺少 ID: {missing_ids[:10]}")
                    translation = Translation(
                        source=source,
                        model=config.ollama_model,
                        created_at=(
                            partial.created_at
                            if partial is not None
                            else datetime.now(UTC).isoformat()
                        ),
                        batches=tuple(batches),
                        items=tuple(item_by_id[item_id] for item_id in expected_ids),
                    )
                    atomic_write_json(translation_path, translation.to_dict())
                    partial_path.unlink(missing_ok=True)
                    report.translated += 1
                content = render_lrc(filtered.accepted, translation.items)
                write_lrc_atomic(item.lrc, content, overwrite=config.overwrite)
                _log(log_path, f"LRC written: {item.lrc}")
                report.succeeded += 1
            except (OSError, ValueError, json.JSONDecodeError, AsmrLrcError) as exc:
                report.add_failure(str(item.audio), str(exc))
                _log(log_path, f"translation/output failure: {exc}")
                print(f"失败: {item.audio}: {exc}", flush=True)
    finally:
        if release_ollama and contexts:
            try:
                unload_model(config.ollama_url, config.ollama_model)
            except AsmrLrcError as exc:
                print(f"警告: {exc}", flush=True)
    return report
