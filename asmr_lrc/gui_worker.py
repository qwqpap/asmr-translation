"""JSONL adapter over :mod:`asmr_lrc.session`.

This module owns only the wire protocol: read one JSON request from stdin, emit
newline-delimited events on stdout, and translate control messages (cancel,
external-API consent) into the pipeline's callbacks.  Every decision about what
the request *means* lives in :mod:`asmr_lrc.session`, shared with the Qt GUI.

The protocol is kept for the existing native front-end and for scripting; the Qt
GUI bypasses it entirely by importing ``session`` directly.
"""

from __future__ import annotations

import json
import os
import signal
import sys
import threading
from pathlib import Path
from typing import Any

from .control import CancelledError, CancelToken
from .pipeline import run_pipeline
from .platform_paths import cache_directory as default_cache_directory
from .session import (
    DEFAULT_PLAYER_CACHE_BYTES,
    build_app_config,
    build_download_config,
    load_cues,
    plan_download,
    prepare_playback,
    probe,
    resolve_download_plan,
    run_download,
    save_edits,
)

PROTOCOL_VERSION = 1
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


def _cache_root(request: dict[str, Any]) -> Path:
    raw = request.get("cache_root")
    if raw in (None, ""):
        return default_cache_directory()
    return Path(str(raw)).expanduser().resolve()


def _command_probe(request: dict[str, Any]) -> None:
    _write("probe_result", result=probe(build_app_config(dict(request.get("config", {})))))


def _command_run(request: dict[str, Any]) -> int:
    config = build_app_config(dict(request.get("config", {})))
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
    config = build_download_config(request)
    raw_rj = request.get("rj", request.get("rj_id", ""))
    if not isinstance(raw_rj, str) or not raw_rj.strip():
        raise ValueError("download_plan 缺少 RJ 编号")
    _TOKEN.raise_if_cancelled()
    # The GUI receives stable IDs and metadata only.  Media URLs remain inside the
    # download worker and are never printed to logs or persisted in the manifest.
    public = plan_download(raw_rj, config)
    _TOKEN.raise_if_cancelled()
    _write("download_metadata", plan=public, **public)


def _command_download_run(request: dict[str, Any]) -> None:
    config = build_download_config(request)
    raw_rj = request.get("rj", request.get("rj_id", ""))
    rj_value = raw_rj if isinstance(raw_rj, str) else ""
    raw_plan = request.get("plan")
    if not isinstance(raw_plan, dict) and not rj_value.strip():
        raise ValueError("download_run 缺少 plan 或 RJ 编号")
    plan = resolve_download_plan(raw_plan if isinstance(raw_plan, dict) else None, rj_value, config)
    selected_raw = request.get("selected_ids", request.get("files", []))
    if not isinstance(selected_raw, list | tuple | set):
        raise ValueError("selected_ids 必须是数组")
    selected_ids = {str(value) for value in selected_raw}
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

    run_download(plan, selected_ids, root, config, token=_TOKEN, callback=callback)


def _command_load_cues(request: dict[str, Any]) -> None:
    audio = Path(str(request["audio"])).resolve()
    _write("cues", **load_cues(audio, _cache_root(request)))


def _command_save_edits(request: dict[str, Any]) -> None:
    audio = Path(str(request["audio"])).resolve()
    edits_raw = request.get("edits", [])
    if not isinstance(edits_raw, list):
        raise ValueError("edits 必须是数组")
    _write("saved", **save_edits(audio, _cache_root(request), edits_raw))


def _command_prepare_playback(request: dict[str, Any]) -> None:
    audio = Path(str(request["audio"])).resolve()
    result = prepare_playback(
        audio,
        _cache_root(request),
        ffmpeg_path=str(request.get("ffmpeg_path", "ffmpeg")),
        limit_bytes=int(request.get("limit_bytes", DEFAULT_PLAYER_CACHE_BYTES)),
    )
    _write("playback_ready", **result)


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
        # reader blocked on an open anonymous stdin pipe prevents this worker from
        # exiting after one-shot commands such as probe/load/save.
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
