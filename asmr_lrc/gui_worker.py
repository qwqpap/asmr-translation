from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
from dataclasses import replace
from pathlib import Path
from typing import Any

from .cache import atomic_write_json, cache_directory, load_validated, source_identity
from .config import AppConfig
from .control import CancelledError, CancelToken
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
from .pipeline import run_pipeline
from .providers import ProviderConfig, create_provider
from .translation_context import load_pinned_glossary

PROTOCOL_VERSION = 1
_LRC_LINE = re.compile(r"^\[(?P<minute>\d+):(?P<second>\d{2})\.(?P<centi>\d{2})](?P<text>.*)$")
_TOKEN = CancelToken()
_CONSENT_CONDITION = threading.Condition()
_CONSENT_RESULT: bool | None = None


def _write(event: str, **data: object) -> None:
    payload = {"protocol": PROTOCOL_VERSION, "event": event, **data}
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def _signal_cancel(_signum: int, _frame: object) -> None:
    _TOKEN.cancel()


def _control_listener() -> None:
    global _CONSENT_RESULT
    pending = b""
    input_fd = sys.stdin.fileno()
    while True:
        try:
            chunk = os.read(input_fd, 4096)
        except OSError:
            return
        if not chunk:
            break
        pending += chunk
        while b"\n" in pending:
            line, pending = pending.split(b"\n", 1)
            _handle_control_line(line)
    if pending:
        _handle_control_line(pending)


def _handle_control_line(line: bytes) -> None:
    global _CONSENT_RESULT
    try:
        request = json.loads(line.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        return
    if not isinstance(request, dict):
        return
    command = request.get("command")
    if command == "cancel":
        _TOKEN.cancel()
    elif command == "consent":
        with _CONSENT_CONDITION:
            _CONSENT_RESULT = bool(request.get("approved", False))
            _CONSENT_CONDITION.notify_all()


def _external_consent(estimated_characters: int) -> bool:
    global _CONSENT_RESULT
    with _CONSENT_CONDITION:
        _CONSENT_RESULT = None
    with _CONSENT_CONDITION:
        while _CONSENT_RESULT is None:
            if _TOKEN.cancelled:
                raise CancelledError("等待外部 API 授权时任务被取消")
            _CONSENT_CONDITION.wait(timeout=0.2)
        return _CONSENT_RESULT


def _provider(data: dict[str, Any], *, fallback: ProviderConfig | None = None) -> ProviderConfig:
    if not data and fallback is not None:
        return fallback
    return ProviderConfig(
        kind=str(data.get("kind", "ollama")),
        base_url=str(data.get("base_url", "http://127.0.0.1:11434")),
        model=str(data.get("model", "qwen3.5-9b-abliterated:latest")),
        api_key=(None if data.get("api_key") is None else str(data["api_key"])),
        strict_schema=bool(data.get("strict_schema", True)),
        timeout_seconds=float(data.get("timeout_seconds", 600)),
        keep_alive=str(data.get("keep_alive", "5m")),
    )


def _config(data: dict[str, Any]) -> AppConfig:
    draft = _provider(dict(data.get("draft_provider", {})))
    review_data = data.get("review_provider")
    review = draft if review_data == "same" else None
    if isinstance(review_data, dict):
        review = _provider(review_data, fallback=draft)
    review_enabled = bool(data.get("review_enabled", True))
    glossary_path = str(data.get("glossary_path", "")).strip()
    return AppConfig(
        cache_root=Path(str(data.get("cache_root", Path.cwd() / ".cache"))).resolve(),
        asr_model=str(data.get("asr_model", "large-v3")),
        fallback_asr_model=(
            None if data.get("fallback_asr_model") is None else str(data["fallback_asr_model"])
        ),
        device=str(data.get("device", "cuda")),
        compute_type=str(data.get("compute_type", "int8_float16")),
        ffmpeg_path=str(data.get("ffmpeg_path", "ffmpeg")),
        ollama_model=(
            draft.model if draft.kind == "ollama" else "qwen3.5-9b-abliterated:latest"
        ),
        ollama_url=(draft.base_url if draft.kind == "ollama" else "http://127.0.0.1:11434"),
        translation_batch_size=int(data.get("batch_size", 12)),
        translation_retries=int(data.get("translation_retries", 2)),
        translation_prompt_character_limit=int(
            data.get("prompt_character_limit", 24_000)
        ),
        quality_mode=str(data.get("quality_mode", "quality")),
        context_before=int(data.get("context_before", 8)),
        context_after=int(data.get("context_after", 8)),
        review_enabled=review_enabled,
        draft_provider=draft,
        review_provider=review if review_enabled else None,
        pinned_glossary=(
            ()
            if not glossary_path
            else load_pinned_glossary(Path(glossary_path).resolve())
        ),
        overwrite=bool(data.get("overwrite", False)),
    )


def _download_config(data: dict[str, Any]) -> DownloadConfig:
    """Build download settings without ever placing secrets or URLs in logs."""
    source = data.get("download", data.get("config", data))
    if not isinstance(source, dict):
        source = {}
    endpoint = str(source.get("download_endpoint", source.get("endpoint", "https://api.asmr-200.com")))
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


def _command_probe(request: dict[str, Any]) -> None:
    config = _config(dict(request.get("config", {})))
    assert config.draft_provider is not None
    providers = [config.draft_provider]
    if config.review_provider is not None and config.review_provider is not config.draft_provider:
        providers.append(config.review_provider)
    ollama_provider = next(
        (provider for provider in providers if provider.kind == "ollama"), None
    )
    result = probe_environment(
        None if ollama_provider is None else ollama_provider.base_url,
        None if ollama_provider is None else ollama_provider.model,
        ffmpeg_path=config.ffmpeg_path,
    )
    provider_checks: list[dict[str, object]] = []
    for provider_config in providers:
        try:
            create_provider(provider_config).check()
            provider_checks.append(
                {"kind": provider_config.kind, "model": provider_config.model, "ok": True}
            )
        except Exception as exc:
            provider_checks.append(
                {
                    "kind": provider_config.kind,
                    "model": provider_config.model,
                    "ok": False,
                    "detail": str(exc),
                }
            )
    result["provider_checks"] = provider_checks
    result["ok"] = bool(result["ok"]) and all(item["ok"] for item in provider_checks)
    _write("probe_result", result=result)


def _command_run(request: dict[str, Any]) -> int:
    config = _config(dict(request.get("config", {})))
    root = Path(str(request["root"])).resolve()

    def callback(event: dict[str, Any]) -> None:
        event_name = str(event.pop("event"))
        _write(event_name, **event)

    report = run_pipeline(
        root,
        config,
        dry_run=bool(request.get("dry_run", False)),
        transcribe_only=bool(request.get("transcribe_only", False)),
        translate_only=bool(request.get("translate_only", False)),
        release_ollama=not bool(request.get("keep_model", False)),
        event_callback=callback,
        cancel_token=_TOKEN,
        quiet=True,
        external_consent_callback=_external_consent,
    )
    return report.exit_code


def _command_download_plan(request: dict[str, Any]) -> None:
    config = _download_config(request)
    raw_rj = request.get("rj", request.get("rj_id", ""))
    if not isinstance(raw_rj, str) or not raw_rj.strip():
        raise ValueError("download_plan 缺少 RJ 编号")
    _TOKEN.raise_if_cancelled()
    plan = fetch_work_plan(raw_rj, config)
    _TOKEN.raise_if_cancelled()
    # The GUI receives stable IDs and metadata only.  Media URLs remain inside the
    # download worker and are never printed to logs or persisted in the manifest.
    public = plan.public_dict()
    public["smart_selected_ids"] = sorted(smart_audio_selection(plan.files))
    _write("download_metadata", plan=public, **public)


def _plan_for_download(request: dict[str, Any], config: DownloadConfig) -> WorkPlan:
    raw_plan = request.get("plan")
    if not isinstance(raw_plan, dict):
        raw_rj = request.get("rj", request.get("rj_id", ""))
        if not isinstance(raw_rj, str) or not raw_rj.strip():
            raise ValueError("download_run 缺少 plan 或 RJ 编号")
        return fetch_work_plan(raw_rj, config)
    # A public plan is intentionally sufficient: resolve fresh media URLs inside
    # the worker.  Full plans are accepted for callers that already own a plan.
    files = raw_plan.get("files")
    has_media_urls = isinstance(files, list) and any(
        isinstance(item, dict) and item.get("mediaDownloadUrl") for item in files
    )
    if has_media_urls:
        return WorkPlan.from_dict(raw_plan)
    raw_rj = raw_plan.get("rj_id", raw_plan.get("rj"))
    if not isinstance(raw_rj, str) or not raw_rj.strip():
        raise ValueError("download_run plan 缺少 RJ 编号")
    return fetch_work_plan(raw_rj, config)


def _command_download_run(request: dict[str, Any]) -> None:
    config = _download_config(request)
    plan = _plan_for_download(request, config)
    selected_raw = request.get("selected_ids", request.get("files", []))
    if not isinstance(selected_raw, list | tuple | set):
        raise ValueError("selected_ids 必须是数组")
    selected_ids = {str(value) for value in selected_raw}
    known_ids = {item.file_id for item in plan.files}
    unknown = selected_ids - known_ids
    if unknown:
        raise ValueError("下载列表包含未知文件 ID")
    if not selected_ids:
        raise ValueError("至少选择一个下载文件")
    config_root = request.get("config", {})
    if not isinstance(config_root, dict):
        config_root = {}
    root_raw = request.get(
        "output_root",
        request.get("download_root", config_root.get("download_root")),
    )
    if not isinstance(root_raw, str) or not root_raw.strip():
        raise ValueError("download_run 缺少输出目录")
    root = Path(root_raw).expanduser().resolve()

    def callback(event: dict[str, object]) -> None:
        event_name = str(event.pop("event"))
        mapped = {
            "file": "download_file",
            "progress": "download_progress",
            "retry": "download_retry",
            "complete": "download_complete",
        }.get(event_name)
        if mapped is not None:
            # curl diagnostics can echo a signed media URL; keep those details
            # out of the JSONL protocol and GUI logs.
            event.pop("detail", None)
            _write(mapped, **event)

    download_plan(
        plan,
        selected_ids,
        root,
        config,
        token=_TOKEN,
        callback=callback,
    )


def _cache_artifacts(audio: Path, cache_root: Path) -> tuple[FilteredTranscript, Translation]:
    source = source_identity(audio)
    directory = cache_directory(cache_root, source)
    filtered = load_validated(
        directory / "transcript.filtered.json", FilteredTranscript.from_dict
    )
    translation = load_validated(
        directory / "translation.zh-CN.json", Translation.from_dict
    )
    return filtered, translation


def _fallback_lrc(audio: Path) -> list[dict[str, object]]:
    lrc = audio.with_suffix(".lrc")
    if not lrc.exists():
        return []
    cues: list[dict[str, object]] = []
    for index, line in enumerate(lrc.read_text(encoding="utf-8-sig").splitlines()):
        match = _LRC_LINE.fullmatch(line)
        if match is None:
            continue
        start = (
            int(match["minute"]) * 60
            + int(match["second"])
            + int(match["centi"]) / 100
        )
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


def _command_load_cues(request: dict[str, Any]) -> None:
    audio = Path(str(request["audio"])).resolve()
    cache_root = Path(str(request.get("cache_root", Path.cwd() / ".cache"))).resolve()
    try:
        filtered, translation = _cache_artifacts(audio, cache_root)
        by_id = {item.id: item for item in translation.items}
        cues = [
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
        profile_id = translation.profile_id
    except Exception:
        cues = _fallback_lrc(audio)
        profile_id = None
    _write(
        "cues",
        audio=str(audio),
        lrc=str(audio.with_suffix(".lrc")),
        profile_id=profile_id,
        cues=cues,
    )


def _command_save_edits(request: dict[str, Any]) -> None:
    audio = Path(str(request["audio"])).resolve()
    cache_root = Path(str(request.get("cache_root", Path.cwd() / ".cache"))).resolve()
    edits_raw = request.get("edits", [])
    if not isinstance(edits_raw, list):
        raise ValueError("edits 必须是数组")
    edits = {
        str(item["id"]): str(item["text"]).strip()
        for item in edits_raw
        if isinstance(item, dict)
    }
    if any(not value for value in edits.values()):
        raise ValueError("人工译文不能为空")
    filtered, translation = _cache_artifacts(audio, cache_root)
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
    _write("saved", audio=str(audio), lrc=str(lrc), backup=str(backup))


def _enforce_player_cache(directory: Path, limit_bytes: int, keep: Path) -> None:
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


def _cleanup_player_temps(directory: Path, target: Path) -> None:
    for path in directory.glob(f".{target.name}.*.tmp.wav"):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            # Another GUI instance may still be writing its own proxy.
            continue


def _command_prepare_playback(request: dict[str, Any]) -> None:
    audio = Path(str(request["audio"])).resolve()
    cache_root = Path(str(request.get("cache_root", Path.cwd() / ".cache"))).resolve()
    player_cache = (cache_root / "player").resolve()
    player_cache.mkdir(parents=True, exist_ok=True)
    source = source_identity(audio)
    target = player_cache / f"{source.fingerprint[:32]}.wav"
    if not target.exists():
        temporary = player_cache / f".{target.name}.{os.getpid()}.tmp.wav"
        command = [
            str(request.get("ffmpeg_path", "ffmpeg")),
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
    _cleanup_player_temps(player_cache, target)
    limit = int(request.get("limit_bytes", 4 * 1024**3))
    _enforce_player_cache(player_cache, limit, target)
    _write("playback_ready", source=str(audio), path=str(target), cached=True)


def dispatch(request: dict[str, Any]) -> int:
    if int(request.get("protocol", 0)) != PROTOCOL_VERSION:
        raise ValueError("不支持的 GUI worker protocol")
    command = str(request.get("command", ""))
    if command == "probe":
        _command_probe(request)
    elif command == "run":
        return _command_run(request)
    elif command == "download_plan":
        _command_download_plan(request)
    elif command == "download_run":
        _command_download_run(request)
    elif command == "load_cues":
        _command_load_cues(request)
    elif command == "save_edits":
        _command_save_edits(request)
    elif command == "prepare_playback":
        _command_prepare_playback(request)
    else:
        raise ValueError(f"未知 GUI worker 命令: {command}")
    return 0


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="strict", newline="\n")
    signal.signal(signal.SIGINT, _signal_cancel)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, _signal_cancel)
    line = sys.stdin.buffer.readline()
    if not line:
        _write("error", code="missing_request", message="stdin 缺少 JSON 请求")
        return 2
    try:
        request = json.loads(line.decode("utf-8"))
        if not isinstance(request, dict):
            raise ValueError("请求根节点必须是 JSON 对象")
        # Only long-running translation needs cancellation/consent messages. Keeping a
        # reader blocked on an open anonymous stdin pipe prevents this Windows worker
        # from exiting after one-shot commands such as probe/load/save.
        if request.get("command") in {"run", "download_plan", "download_run"}:
            threading.Thread(target=_control_listener, daemon=True).start()
        return dispatch(request)
    except CancelledError as exc:
        _write("cancelled", message=str(exc))
        return 130
    except Exception as exc:
        _write("error", code=type(exc).__name__, message=str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
