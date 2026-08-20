from __future__ import annotations

import subprocess
import sys
import time
import urllib.error
from pathlib import Path

from .control import CancelledError, CancelToken
from .environment import ollama_running_models
from .errors import AsrError
from .process import ProcessTree, child_environment, spawn_kwargs


def assert_ollama_gpu_free(base_url: str) -> None:
    try:
        running = ollama_running_models(base_url)
    except (OSError, ValueError, urllib.error.URLError) as exc:
        raise AsrError(f"开始 ASR 前无法检查 Ollama 运行状态: {exc}") from exc
    if running:
        names = ", ".join(sorted(running))
        raise AsrError(
            f"Ollama 当前已加载模型（{names}）。请先执行 `ollama stop <模型名>`，"
            "确认 `ollama ps` 为空后重试，避免与 Whisper 争用显存。"
        )


def _looks_like_oom(stderr: str) -> bool:
    lowered = stderr.casefold()
    return any(
        marker in lowered
        for marker in ("out of memory", "cuda_error_out_of_memory", "failed to allocate")
    )


def run_asr_process(
    audio: Path,
    output: Path,
    log_path: Path,
    *,
    model: str,
    device: str,
    compute_type: str,
    ollama_url: str | None,
    cancel_token: CancelToken | None = None,
) -> None:
    if ollama_url is not None:
        assert_ollama_gpu_free(ollama_url)
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        "-m",
        "asmr_lrc.asr_worker",
        "--audio",
        str(audio),
        "--output",
        str(output),
        "--model",
        model,
        "--device",
        device,
        "--compute-type",
        compute_type,
    ]
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=child_environment(),
            **spawn_kwargs(),
        )
    except OSError as exc:
        raise AsrError(f"无法启动 ASR 子进程: {exc}") from exc
    # A cancelled run must not leave CUDA memory pinned by an orphaned worker, so
    # the child owns its own process tree and is torn down as a whole.
    tree = ProcessTree(process)
    try:
        while process.poll() is None:
            if cancel_token is not None and cancel_token.cancelled:
                tree.stop()
                raise CancelledError("ASR 已取消")
            time.sleep(0.2)
        stdout, stderr = process.communicate()
    finally:
        tree.close()
    debug = (stdout + "\n" + stderr).strip()
    with log_path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(f"ASR command model={model} device={device} compute_type={compute_type}\n")
        if debug:
            stream.write(debug + "\n")
    if process.returncode == 0 and output.exists():
        return
    if _looks_like_oom(stderr):
        raise AsrError(
            f"ASR 模型 {model} 显存不足。可显式指定 `--fallback-asr-model medium` "
            f"或直接使用 `--asr-model medium`。调试日志: {log_path}"
        )
    raise AsrError(f"ASR 子进程失败（退出码 {process.returncode}）。调试日志: {log_path}")
