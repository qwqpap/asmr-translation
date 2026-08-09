from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from .asr import run_asr_process
from .cache import (
    atomic_write_json,
    cache_directory,
    load_json,
    load_validated,
    quarantine_corrupt,
    quarantine_stale,
    same_source,
    source_identity,
)
from .config import AppConfig
from .control import CancelledError, CancelToken, EventCallback, emit
from .errors import AsmrLrcError, CacheError
from .filters import filter_transcript
from .lrc import render_lrc, write_lrc_atomic
from .models import FilteredTranscript, SourceIdentity, Transcript, Translation
from .providers import TranslationProvider, create_provider
from .reporting import BatchReport
from .scanner import plan_scan
from .translation_context import (
    CONTEXT_PROMPT_VERSION,
    CONTEXT_SCHEMA_VERSION,
    ContextMemory,
    analyze_context,
    baseline_context_memory,
)
from .translator import translate_contextual_batch


def _log(path: Path, message: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).isoformat()
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(f"{stamp} {message}\n")


def _message(
    message: str,
    *,
    callback: EventCallback | None,
    quiet: bool,
    level: str = "info",
    **data: object,
) -> None:
    if not quiet:
        print(message, flush=True)
    emit(callback, "log", level=level, message=message, **data)


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
        and translation.profile_id == config.translation_profile_id()
        and translation.stage == "final"
        and actual == expected
        and all(item.text.strip() for item in translation.items)
    )


def _translation_partial_valid(
    translation: Translation,
    filtered: FilteredTranscript,
    config: AppConfig,
    *,
    stage: str,
) -> bool:
    expected = {item.id for item in filtered.accepted}
    actual = [item.id for item in translation.items]
    return (
        same_source(translation.source, filtered.source)
        and translation.profile_id == config.translation_profile_id()
        and translation.stage == stage
        and len(actual) == len(set(actual))
        and set(actual).issubset(expected)
        and all(item.text.strip() for item in translation.items)
    )


def _load_translation_or_none(path: Path, log_path: Path) -> Translation | None:
    if not path.exists():
        return None
    try:
        data = load_json(path)
        if int(data.get("schema_version", 0)) != 2:
            stale = quarantine_stale(path)
            _log(log_path, f"translation cache stale: {stale.name}")
            return None
        return Translation.from_dict(data)
    except (CacheError, KeyError, TypeError, ValueError) as exc:
        quarantined = quarantine_corrupt(path)
        _log(log_path, f"translation cache corrupt: {quarantined.name}: {exc}")
        return None


def _load_context_memory(
    path: Path,
    *,
    source: SourceIdentity,
    profile_id: str,
    log_path: Path,
) -> ContextMemory | None:
    if not path.exists():
        return None
    try:
        data = load_json(path)
        if int(data.get("schema_version", 0)) != CONTEXT_SCHEMA_VERSION:
            stale = quarantine_stale(path)
            _log(log_path, f"context cache stale: {stale.name}")
            return None
        cached_source = SourceIdentity.from_dict(data["source"])
        if not same_source(cached_source, source) or data.get("profile_id") != profile_id:
            return None
        return ContextMemory.from_dict(data["memory"])
    except (CacheError, KeyError, TypeError, ValueError) as exc:
        quarantined = quarantine_corrupt(path)
        _log(log_path, f"context cache corrupt: {quarantined.name}: {exc}")
        return None


def _translation_record(
    *,
    source: SourceIdentity,
    config: AppConfig,
    stage: str,
    batches: list[dict[str, object]],
    items: tuple,
    created_at: str,
) -> Translation:
    assert config.draft_provider is not None
    review_model = None if config.review_provider is None else config.review_provider.model
    reviewed_stage = stage == "review" or (
        stage == "final" and config.quality_mode == "quality" and config.review_enabled
    )
    model = review_model if reviewed_stage and review_model else config.draft_provider.model
    return Translation(
        source=source,
        model=model,
        created_at=created_at,
        batches=tuple(batches),
        items=items,
        profile_id=config.translation_profile_id(),
        stage=stage,
        draft_model=config.draft_provider.model,
        review_model=review_model,
        prompt_version=CONTEXT_PROMPT_VERSION,
    )


def _provider_pair(config: AppConfig) -> tuple[TranslationProvider, TranslationProvider | None]:
    assert config.draft_provider is not None
    draft = create_provider(config.draft_provider)
    review: TranslationProvider | None = None
    if config.review_enabled and config.review_provider is not None:
        if config.review_provider is config.draft_provider:
            review = draft
        else:
            review = create_provider(config.review_provider)
    return draft, review


def _estimate_external_characters(
    contexts: dict[Path, tuple[SourceIdentity, Path, Transcript, FilteredTranscript]],
    config: AppConfig,
) -> int:
    assert config.draft_provider is not None
    total = 0
    for _source, _directory, _transcript, filtered in contexts.values():
        segments = filtered.accepted
        if config.quality_mode == "quality" and config.draft_provider.kind == "openai":
            total += sum(len(segment.text) for segment in segments)
        for offset in range(0, len(segments), config.translation_batch_size):
            start = max(0, offset - config.context_before)
            end = min(
                len(segments),
                offset + config.translation_batch_size + config.context_after,
            )
            window_characters = sum(len(segment.text) for segment in segments[start:end])
            if config.draft_provider.kind == "openai":
                total += window_characters
            if (
                config.quality_mode == "quality"
                and config.review_enabled
                and config.review_provider is not None
                and config.review_provider.kind == "openai"
            ):
                total += window_characters * 2
    return total


def _write_source_metadata(path: Path, source: SourceIdentity) -> None:
    atomic_write_json(
        path,
        {
            "schema_version": 1,
            "source": source.to_dict(),
            "tool_version": "0.2.0",
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
    event_callback: EventCallback | None = None,
    cancel_token: CancelToken | None = None,
    quiet: bool = False,
    external_consent_callback: Callable[[int], bool] | None = None,
) -> BatchReport:
    token = cancel_token or CancelToken()
    plan = plan_scan(root, overwrite=config.overwrite)
    report = BatchReport(total=len(plan))
    emit(
        event_callback,
        "plan",
        items=[
            {"audio": str(item.audio), "lrc": str(item.lrc), "action": item.action}
            for item in plan
        ],
    )
    for item in plan:
        _message(
            f"[{item.action}] {item.audio}",
            callback=event_callback,
            quiet=quiet,
            audio=str(item.audio),
        )
        if item.action == "skip":
            report.skipped += 1
    if dry_run:
        return report

    work = [item for item in plan if item.action != "skip"]
    contexts: dict[Path, tuple[SourceIdentity, Path, Transcript, FilteredTranscript]] = {}

    # Phase 1: all ASR subprocesses finish before Ollama translation starts.
    for index, item in enumerate(work, 1):
        token.raise_if_cancelled()
        emit(
            event_callback,
            "phase",
            phase="asr",
            current=index,
            total=len(work),
            audio=str(item.audio),
        )
        _message(
            f"ASR {index}/{len(work)}: {item.audio}",
            callback=event_callback,
            quiet=quiet,
        )
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
                        _message(
                            f"加载 ASR 模型：{model}",
                            callback=event_callback,
                            quiet=quiet,
                            audio=str(item.audio),
                        )
                        run_asr_process(
                            item.audio,
                            raw_path,
                            log_path,
                            model=model,
                            device=config.device,
                            compute_type=config.compute_type,
                            ollama_url=(
                                config.ollama_url
                                if any(
                                    provider is not None and provider.kind == "ollama"
                                    for provider in (
                                        config.draft_provider,
                                        config.review_provider,
                                    )
                                )
                                else None
                            ),
                            cancel_token=token,
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
        except CancelledError:
            raise
        except (OSError, ValueError, AsmrLrcError) as exc:
            report.add_failure(str(item.audio), str(exc))
            _message(
                f"失败: {item.audio}: {exc}",
                callback=event_callback,
                quiet=quiet,
                level="error",
                audio=str(item.audio),
            )

    if transcribe_only:
        return report

    # Phase 2: Whisper processes have exited; only now may translation models load.
    draft_provider, review_provider = _provider_pair(config)
    providers_checked = False
    external_authorized = False
    try:
        for index, item in enumerate(work, 1):
            token.raise_if_cancelled()
            context = contexts.get(item.audio)
            if context is None:
                continue
            source, directory, _transcript, filtered = context
            translation_path = directory / "translation.zh-CN.json"
            context_path = directory / "translation.context.json"
            draft_path = directory / "translation.zh-CN.draft.json"
            draft_partial_path = directory / "translation.zh-CN.partial.json"
            review_partial_path = directory / "translation.zh-CN.review.partial.json"
            log_path = directory / "process.log"
            emit(
                event_callback,
                "phase",
                phase="translation",
                current=index,
                total=len(work),
                audio=str(item.audio),
            )
            _message(
                f"翻译 {index}/{len(work)}: {item.audio}",
                callback=event_callback,
                quiet=quiet,
            )
            try:
                translation = _load_translation_or_none(translation_path, log_path)
                if translation is not None and _translation_valid(translation, filtered, config):
                    report.cache_hits += 1
                else:
                    translation = None
                if translation is None:
                    uses_external = any(
                        provider is not None and provider.kind == "openai"
                        for provider in (config.draft_provider, config.review_provider)
                    )
                    if uses_external and not external_authorized:
                        estimated = _estimate_external_characters(contexts, config)
                        emit(
                            event_callback,
                            "external_consent_required",
                            estimated_characters=estimated,
                            audio_uploaded=False,
                        )
                        if external_consent_callback is None or not external_consent_callback(
                            estimated
                        ):
                            raise AsmrLrcError("用户未授权向外部 API 发送转写文本")
                        external_authorized = True
                    if not providers_checked:
                        draft_provider.check()
                        if review_provider is not None and review_provider is not draft_provider:
                            review_provider.check()
                        providers_checked = True

                    profile_id = config.translation_profile_id()
                    expected_ids = tuple(segment.id for segment in filtered.accepted)
                    memory = _load_context_memory(
                        context_path,
                        source=source,
                        profile_id=profile_id,
                        log_path=log_path,
                    )
                    if memory is None:
                        if config.quality_mode == "quality":
                            token.raise_if_cancelled()
                            emit(
                                event_callback,
                                "phase",
                                phase="context",
                                current=index,
                                total=len(work),
                                audio=str(item.audio),
                            )
                            _message(
                                f"语境分析: {item.audio.name}",
                                callback=event_callback,
                                quiet=quiet,
                            )
                            memory, context_metrics = analyze_context(
                                filtered.accepted,
                                provider=draft_provider,
                                retries=config.translation_retries,
                                pinned_terms=config.pinned_glossary,
                                max_characters=min(
                                    12_000,
                                    config.translation_prompt_character_limit,
                                ),
                            )
                        else:
                            memory = baseline_context_memory(
                                filtered.accepted,
                                config.pinned_glossary,
                            )
                            context_metrics = ()
                        atomic_write_json(
                            context_path,
                            {
                                "schema_version": CONTEXT_SCHEMA_VERSION,
                                "source": source.to_dict(),
                                "profile_id": profile_id,
                                "created_at": datetime.now(UTC).isoformat(),
                                "memory": memory.to_dict(),
                                "batches": list(context_metrics),
                            },
                        )
                    else:
                        report.cache_hits += 1

                    draft = _load_translation_or_none(draft_path, log_path)
                    if draft is not None and not (
                        _translation_partial_valid(
                            draft, filtered, config, stage="draft"
                        )
                        and tuple(part.id for part in draft.items) == expected_ids
                    ):
                        draft = None
                    if draft is None:
                        partial = _load_translation_or_none(draft_partial_path, log_path)
                        if partial is not None and not _translation_partial_valid(
                            partial, filtered, config, stage="draft"
                        ):
                            partial = None
                        item_by_id = (
                            {} if partial is None else {part.id: part for part in partial.items}
                        )
                        batches = [] if partial is None else list(partial.batches)
                        created_at = (
                            datetime.now(UTC).isoformat()
                            if partial is None
                            else partial.created_at
                        )
                    else:
                        item_by_id = {part.id: part for part in draft.items}
                        batches = list(draft.batches)
                        created_at = draft.created_at
                        report.cache_hits += 1

                    total_batches = (
                        len(filtered.accepted) + config.translation_batch_size - 1
                    ) // config.translation_batch_size
                    for offset in range(0, len(filtered.accepted), config.translation_batch_size):
                        token.raise_if_cancelled()
                        batch_number = offset // config.translation_batch_size + 1
                        indices = tuple(
                            position
                            for position in range(
                                offset,
                                min(
                                    len(filtered.accepted),
                                    offset + config.translation_batch_size,
                                ),
                            )
                            if filtered.accepted[position].id not in item_by_id
                        )
                        if not indices:
                            continue
                        emit(
                            event_callback,
                            "batch",
                            stage="draft",
                            current=batch_number,
                            total=total_batches,
                            audio=str(item.audio),
                        )
                        _message(
                            f"初译批次 {batch_number}/{total_batches}: {item.audio.name}",
                            callback=event_callback,
                            quiet=quiet,
                        )
                        new_items, batch_metrics = translate_contextual_batch(
                            filtered.accepted,
                            indices,
                            provider=draft_provider,
                            memory=memory,
                            context_before=config.context_before,
                            context_after=config.context_after,
                            retries=config.translation_retries,
                            max_prompt_characters=config.translation_prompt_character_limit,
                        )
                        item_by_id.update({part.id: part for part in new_items})
                        batches.append(batch_metrics)
                        partial = _translation_record(
                            source=source,
                            config=config,
                            stage="draft",
                            batches=batches,
                            items=tuple(
                                item_by_id[item_id]
                                for item_id in expected_ids
                                if item_id in item_by_id
                            ),
                            created_at=created_at,
                        )
                        atomic_write_json(draft_partial_path, partial.to_dict())
                    if set(item_by_id) != set(expected_ids):
                        missing_ids = sorted(set(expected_ids) - set(item_by_id))
                        raise AsmrLrcError(f"初译未完成，缺少 ID: {missing_ids[:10]}")
                    draft = _translation_record(
                        source=source,
                        config=config,
                        stage="draft",
                        batches=batches,
                        items=tuple(item_by_id[item_id] for item_id in expected_ids),
                        created_at=created_at,
                    )
                    atomic_write_json(draft_path, draft.to_dict())
                    draft_partial_path.unlink(missing_ok=True)

                    do_review = (
                        config.quality_mode == "quality"
                        and config.review_enabled
                        and review_provider is not None
                    )
                    final_items = draft.items
                    final_batches = list(draft.batches)
                    if do_review:
                        review_partial = _load_translation_or_none(
                            review_partial_path, log_path
                        )
                        if review_partial is not None and not _translation_partial_valid(
                            review_partial, filtered, config, stage="review"
                        ):
                            review_partial = None
                        reviewed_by_id = (
                            {}
                            if review_partial is None
                            else {part.id: part for part in review_partial.items}
                        )
                        review_batches = (
                            [] if review_partial is None else list(review_partial.batches)
                        )
                        drafts = {part.id: part.text for part in draft.items}
                        for offset in range(
                            0, len(filtered.accepted), config.translation_batch_size
                        ):
                            token.raise_if_cancelled()
                            batch_number = offset // config.translation_batch_size + 1
                            indices = tuple(
                                position
                                for position in range(
                                    offset,
                                    min(
                                        len(filtered.accepted),
                                        offset + config.translation_batch_size,
                                    ),
                                )
                                if filtered.accepted[position].id not in reviewed_by_id
                            )
                            if not indices:
                                continue
                            emit(
                                event_callback,
                                "batch",
                                stage="review",
                                current=batch_number,
                                total=total_batches,
                                audio=str(item.audio),
                            )
                            _message(
                                f"审校批次 {batch_number}/{total_batches}: {item.audio.name}",
                                callback=event_callback,
                                quiet=quiet,
                            )
                            reviewed, batch_metrics = translate_contextual_batch(
                                filtered.accepted,
                                indices,
                                provider=review_provider,
                                memory=memory,
                                context_before=config.context_before,
                                context_after=config.context_after,
                                retries=config.translation_retries,
                                drafts=drafts,
                                max_prompt_characters=(
                                    config.translation_prompt_character_limit
                                ),
                            )
                            reviewed_by_id.update({part.id: part for part in reviewed})
                            review_batches.append(batch_metrics)
                            review_partial = _translation_record(
                                source=source,
                                config=config,
                                stage="review",
                                batches=review_batches,
                                items=tuple(
                                    reviewed_by_id[item_id]
                                    for item_id in expected_ids
                                    if item_id in reviewed_by_id
                                ),
                                created_at=created_at,
                            )
                            atomic_write_json(review_partial_path, review_partial.to_dict())
                        if set(reviewed_by_id) != set(expected_ids):
                            missing_ids = sorted(set(expected_ids) - set(reviewed_by_id))
                            raise AsmrLrcError(
                                f"审校未完成，缺少 ID: {missing_ids[:10]}"
                            )
                        final_items = tuple(
                            reviewed_by_id[item_id] for item_id in expected_ids
                        )
                        final_batches.extend(review_batches)

                    translation = _translation_record(
                        source=source,
                        config=config,
                        stage="final",
                        batches=final_batches,
                        items=final_items,
                        created_at=created_at,
                    )
                    atomic_write_json(translation_path, translation.to_dict())
                    review_partial_path.unlink(missing_ok=True)
                    report.translated += 1
                content = render_lrc(filtered.accepted, translation.items)
                write_lrc_atomic(item.lrc, content, overwrite=config.overwrite)
                _log(log_path, f"LRC written: {item.lrc}")
                report.succeeded += 1
            except CancelledError:
                raise
            except (OSError, ValueError, json.JSONDecodeError, AsmrLrcError) as exc:
                report.add_failure(str(item.audio), str(exc))
                _log(log_path, f"translation/output failure: {exc}")
                _message(
                    f"失败: {item.audio}: {exc}",
                    callback=event_callback,
                    quiet=quiet,
                    level="error",
                    audio=str(item.audio),
                )
    finally:
        if release_ollama and contexts:
            providers = [draft_provider]
            if review_provider is not None and review_provider is not draft_provider:
                providers.append(review_provider)
            for provider in providers:
                try:
                    provider.unload()
                except AsmrLrcError as exc:
                    _message(
                        f"警告: {exc}",
                        callback=event_callback,
                        quiet=quiet,
                        level="warning",
                    )
    emit(event_callback, "result", report=asdict(report), exit_code=report.exit_code)
    return report
