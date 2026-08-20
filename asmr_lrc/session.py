"""Shared application logic behind every front-end.

The Win32 GUI could only reach the pipeline through a JSONL subprocess protocol,
so this logic lived inside ``gui_worker``.  The Qt GUI runs in the same process
and calls straight into it, which means the logic has to sit somewhere both can
use -- otherwise the two front-ends drift apart on exactly the details that are
hard to test, like which provider handles context analysis when the draft model
speaks the TranslateGemma protocol.

``gui_worker`` is now a thin JSONL adapter over this module, so the CLI, the JSONL
protocol, and the Qt GUI all resolve configuration the same way.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

from .cache import atomic_write_json, cache_directory, load_validated, source_identity
from .config import (
    DEFAULT_ANALYSIS_MODEL,
    DEFAULT_TRANSLATION_MODEL,
    AppConfig,
    protocol_for_model,
)
from .control import CancelToken
from .downloader import (
    DownloadConfig,
    WorkPlan,
    download_plan,
    fetch_work_plan,
    smart_audio_selection,
)
from .environment import probe_environment
from .lrc import render_lrc, write_lrc_atomic
from .models import FilteredTranscript, Translation
from .platform_paths import cache_directory as default_cache_directory
from .providers import ProviderConfig, create_provider
from .translation_context import load_pinned_glossary

DEFAULT_PLAYER_CACHE_BYTES = 4 * 1024**3
_LRC_LINE = re.compile(r"^\[(?P<minute>\d+):(?P<second>\d{2})\.(?P<centi>\d{2})](?P<text>.*)$")


# --- configuration ---------------------------------------------------------


def build_provider_config(
    data: dict[str, Any], *, fallback: ProviderConfig | None = None
) -> ProviderConfig:
    if not data and fallback is not None:
        return fallback
    model = str(data.get("model", DEFAULT_TRANSLATION_MODEL))
    raw_protocol = data.get("protocol")
    return ProviderConfig(
        kind=str(data.get("kind", "ollama")),
        base_url=str(data.get("base_url", "http://127.0.0.1:11434")),
        model=model,
        api_key=(None if data.get("api_key") is None else str(data["api_key"])),
        strict_schema=bool(data.get("strict_schema", True)),
        timeout_seconds=float(data.get("timeout_seconds", 600)),
        keep_alive=str(data.get("keep_alive", "5m")),
        protocol=(str(raw_protocol) if raw_protocol else protocol_for_model(model)),
    )


def build_app_config(data: dict[str, Any]) -> AppConfig:
    """Resolve every translation role from a front-end settings payload."""
    draft = build_provider_config(dict(data.get("draft_provider", {})))
    analysis_data = data.get("analysis_provider")
    analysis = None
    if isinstance(analysis_data, dict):
        analysis = build_provider_config(analysis_data)
    elif analysis_data == "same":
        analysis = draft
    elif draft.protocol == "translategemma":
        # TranslateGemma's prompt contract is translation-only, so context
        # analysis must never silently fall through to it.
        analysis = ProviderConfig(
            "ollama",
            draft.base_url,
            DEFAULT_ANALYSIS_MODEL,
            keep_alive="5m",
            protocol="chat-json",
        )
    fallback_data = data.get("fallback_provider")
    fallback = None
    if isinstance(fallback_data, dict):
        fallback = build_provider_config(fallback_data)
    elif fallback_data == "same" or analysis is not None:
        fallback = analysis
    review_data = data.get("review_provider")
    review = draft if review_data == "same" else None
    if isinstance(review_data, dict):
        review = build_provider_config(review_data, fallback=draft)
    review_enabled = bool(data.get("review_enabled", draft.protocol != "translategemma"))
    glossary_path = str(data.get("glossary_path", "")).strip()
    raw_cache_root = data.get("cache_root")
    cache_root = (
        default_cache_directory()
        if raw_cache_root in (None, "")
        else Path(str(raw_cache_root))
    )
    return AppConfig(
        cache_root=cache_root.expanduser().resolve(),
        asr_model=str(data.get("asr_model", "large-v3")),
        fallback_asr_model=(
            None if data.get("fallback_asr_model") is None else str(data["fallback_asr_model"])
        ),
        device=str(data.get("device", "cuda")),
        compute_type=str(data.get("compute_type", "int8_float16")),
        ffmpeg_path=str(data.get("ffmpeg_path", "ffmpeg")),
        ollama_model=(draft.model if draft.kind == "ollama" else DEFAULT_ANALYSIS_MODEL),
        ollama_url=(draft.base_url if draft.kind == "ollama" else "http://127.0.0.1:11434"),
        translation_batch_size=int(data.get("batch_size", 12)),
        translation_retries=int(data.get("translation_retries", 2)),
        translation_prompt_character_limit=int(data.get("prompt_character_limit", 24_000)),
        quality_mode=str(data.get("quality_mode", "quality")),
        context_before=int(data.get("context_before", 8)),
        context_after=int(data.get("context_after", 8)),
        review_enabled=review_enabled,
        draft_provider=draft,
        review_provider=review if review_enabled else None,
        analysis_provider=analysis,
        fallback_provider=fallback,
        pinned_glossary=(
            () if not glossary_path else load_pinned_glossary(Path(glossary_path).resolve())
        ),
        overwrite=bool(data.get("overwrite", False)),
    )


def build_download_config(data: dict[str, Any]) -> DownloadConfig:
    """Build download settings without ever placing secrets or URLs in logs."""
    source = data.get("download", data.get("config", data))
    if not isinstance(source, dict):
        source = {}
    endpoint = str(
        source.get("download_endpoint", source.get("endpoint", "https://api.asmr-200.com"))
    )
    curl_path_raw = source.get("curl_path")
    proxy_raw = source.get("download_proxy", source.get("proxy"))
    timeout_raw = source.get("download_connect_timeout", source.get("connect_timeout", 10))
    retries_raw = source.get("download_retries", source.get("max_retries", 5))
    try:
        timeout = int(timeout_raw)
        retries = int(retries_raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("下载超时和重试次数必须是数字") from exc
    return DownloadConfig(
        endpoint=endpoint,
        curl_path=None if curl_path_raw in (None, "") else str(curl_path_raw),
        proxy=None if proxy_raw in (None, "") else str(proxy_raw),
        connect_timeout=timeout,
        max_retries=retries,
    )


def configured_providers(config: AppConfig) -> list[ProviderConfig]:
    assert config.draft_provider is not None
    providers = [config.draft_provider]
    for provider in (config.review_provider, config.analysis_provider, config.fallback_provider):
        if provider is not None and provider not in providers:
            providers.append(provider)
    return providers


# --- dependency probe ------------------------------------------------------


def probe(config: AppConfig) -> dict[str, Any]:
    """Check every dependency and model, installing nothing."""
    providers = configured_providers(config)
    ollama_provider = next((provider for provider in providers if provider.kind == "ollama"), None)
    result = probe_environment(
        None if ollama_provider is None else ollama_provider.base_url,
        None if ollama_provider is None else ollama_provider.model,
        ffmpeg_path=config.ffmpeg_path,
        device=config.device,
    )
    provider_checks: list[dict[str, object]] = []
    for provider_config in providers:
        try:
            create_provider(provider_config).check()
            provider_checks.append(
                {"kind": provider_config.kind, "model": provider_config.model, "ok": True}
            )
        except Exception as exc:
            item: dict[str, object] = {
                "kind": provider_config.kind,
                "model": provider_config.model,
                "ok": False,
                "detail": str(exc),
            }
            if provider_config.kind == "ollama" and "模型未安装" in str(exc):
                # Never pull automatically; hand the user the exact command.
                item["install_command"] = f"ollama pull {provider_config.model}"
            provider_checks.append(item)
    result["provider_checks"] = provider_checks
    result["ok"] = bool(result["ok"]) and all(item["ok"] for item in provider_checks)
    return result


# --- cues and edits --------------------------------------------------------


def cache_artifacts(audio: Path, cache_root: Path) -> tuple[FilteredTranscript, Translation]:
    source = source_identity(audio)
    directory = cache_directory(cache_root, source)
    filtered = load_validated(directory / "transcript.filtered.json", FilteredTranscript.from_dict)
    translation = load_validated(directory / "translation.zh-CN.json", Translation.from_dict)
    return filtered, translation


def parse_lrc_cues(audio: Path) -> list[dict[str, object]]:
    """Read an existing LRC when the cache is gone, so playback still works."""
    lrc = audio.with_suffix(".lrc")
    if not lrc.exists():
        return []
    cues: list[dict[str, object]] = []
    for index, line in enumerate(lrc.read_text(encoding="utf-8-sig").splitlines()):
        match = _LRC_LINE.fullmatch(line)
        if match is None:
            continue
        start = int(match["minute"]) * 60 + int(match["second"]) + int(match["centi"]) / 100
        cues.append(
            {
                "id": f"lrc-{index + 1}",
                "start": start,
                "end": None,
                "source": "",
                "text": match["text"].strip(),
                "flags": [],
            }
        )
    return cues


def load_cues(audio: Path, cache_root: Path) -> dict[str, Any]:
    """Return playable cues, preferring the cache over the rendered LRC."""
    try:
        filtered, translation = cache_artifacts(audio, cache_root)
        by_id = {item.id: item for item in translation.items}
        cues: list[dict[str, object]] = [
            {
                "id": segment.id,
                "start": segment.start,
                "end": segment.end,
                "source": segment.text,
                "text": by_id[segment.id].text,
                "flags": list(by_id[segment.id].flags),
            }
            for segment in filtered.accepted
            if segment.id in by_id
        ]
        profile_id: str | None = translation.profile_id
    except Exception:
        cues = parse_lrc_cues(audio)
        profile_id = None
    return {
        "audio": str(audio),
        "lrc": str(audio.with_suffix(".lrc")),
        "profile_id": profile_id,
        "cues": cues,
    }


def save_edits(
    audio: Path, cache_root: Path, edits_raw: list[dict[str, Any]]
) -> dict[str, Any]:
    """Rewrite the LRC and cache from manual edits, backing up once."""
    edits = {
        str(item["id"]): str(item["text"]).strip()
        for item in edits_raw
        if isinstance(item, dict)
    }
    if any(not value for value in edits.values()):
        raise ValueError("人工译文不能为空")
    filtered, translation = cache_artifacts(audio, cache_root)
    expected = {segment.id for segment in filtered.accepted}
    if not set(edits).issubset(expected):
        raise ValueError("人工修改包含未知台词 ID")
    updated_items = tuple(
        replace(
            item,
            text=edits.get(item.id, item.text),
            flags=(
                tuple(dict.fromkeys(item.flags + ("manual_edited",)))
                if item.id in edits and edits[item.id] != item.text
                else item.flags
            ),
        )
        for item in translation.items
    )
    content = render_lrc(filtered.accepted, updated_items)
    lrc = audio.with_suffix(".lrc")
    backup = lrc.with_name(lrc.name + ".bak")
    if lrc.exists() and not backup.exists():
        shutil.copy2(lrc, backup)
    write_lrc_atomic(lrc, content, overwrite=True)
    source = source_identity(audio)
    directory = cache_directory(cache_root, source)
    atomic_write_json(
        directory / "translation.zh-CN.json",
        replace(translation, items=updated_items).to_dict(),
    )
    return {"audio": str(audio), "lrc": str(lrc), "backup": str(backup)}


# --- playback proxy --------------------------------------------------------


def enforce_player_cache(directory: Path, limit_bytes: int, keep: Path) -> None:
    files = sorted(
        (path for path in directory.glob("*.wav") if path.is_file()),
        key=lambda path: path.stat().st_mtime_ns,
    )
    total = sum(path.stat().st_size for path in files)
    for path in files:
        if total <= limit_bytes:
            break
        if path == keep:
            continue
        size = path.stat().st_size
        path.unlink(missing_ok=True)
        total -= size


def cleanup_player_temps(directory: Path, target: Path) -> None:
    for path in directory.glob(f".{target.name}.*.tmp.wav"):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            # Another instance may still be writing its own proxy.
            continue


def prepare_playback(
    audio: Path,
    cache_root: Path,
    *,
    ffmpeg_path: str = "ffmpeg",
    limit_bytes: int = DEFAULT_PLAYER_CACHE_BYTES,
) -> dict[str, Any]:
    """Render a PCM WAV proxy so the player needs no codec of its own."""
    player_cache = (cache_root / "player").resolve()
    player_cache.mkdir(parents=True, exist_ok=True)
    source = source_identity(audio)
    target = player_cache / f"{source.fingerprint[:32]}.wav"
    cached = target.exists()
    if not cached:
        temporary = player_cache / f".{target.name}.{os.getpid()}.tmp.wav"
        command = [
            ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(audio),
            "-vn",
            "-c:a",
            "pcm_s16le",
            str(temporary),
        ]
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0 or not temporary.exists():
            temporary.unlink(missing_ok=True)
            raise RuntimeError(f"FFmpeg 播放代理生成失败: {result.stderr.strip()}")
        os.replace(temporary, target)
    cleanup_player_temps(player_cache, target)
    enforce_player_cache(player_cache, limit_bytes, target)
    return {"source": str(audio), "path": str(target), "cached": True}


# --- downloads -------------------------------------------------------------


def plan_download(rj_value: str, config: DownloadConfig) -> dict[str, Any]:
    """Fetch work metadata; media URLs stay inside this process."""
    if not rj_value.strip():
        raise ValueError("缺少 RJ 编号")
    plan = fetch_work_plan(rj_value, config)
    public = plan.public_dict()
    public["smart_selected_ids"] = sorted(smart_audio_selection(plan.files))
    return public


def resolve_download_plan(
    raw_plan: dict[str, Any] | None, rj_value: str, config: DownloadConfig
) -> WorkPlan:
    if not isinstance(raw_plan, dict):
        if not rj_value.strip():
            raise ValueError("缺少 plan 或 RJ 编号")
        return fetch_work_plan(rj_value, config)
    files = raw_plan.get("files")
    has_media_urls = isinstance(files, list) and any(
        isinstance(item, dict) and item.get("mediaDownloadUrl") for item in files
    )
    if has_media_urls:
        return WorkPlan.from_dict(raw_plan)
    candidate = str(raw_plan.get("rj_id", raw_plan.get("rj", rj_value)) or "")
    if not candidate.strip():
        raise ValueError("plan 缺少 RJ 编号")
    return fetch_work_plan(candidate, config)


def run_download(
    plan: WorkPlan,
    selected_ids: set[str],
    root: Path,
    config: DownloadConfig,
    *,
    token: CancelToken | None = None,
    callback: Callable[[dict[str, object]], None] | None = None,
) -> Path:
    known_ids = {item.file_id for item in plan.files}
    unknown = selected_ids - known_ids
    if unknown:
        raise ValueError("下载列表包含未知文件 ID")
    if not selected_ids:
        raise ValueError("至少选择一个下载文件")
    return download_plan(plan, selected_ids, root, config, token=token, callback=callback)
