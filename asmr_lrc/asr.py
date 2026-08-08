from __future__ import annotations

import subprocess
import sys
import urllib.error
from pathlib import Path

from .environment import ollama_running_models
from .errors import AsrError


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
    ollama_url: str,
) -> None:
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
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        raise AsrError(f"无法启动 ASR 子进程: {exc}") from exc
    debug = (result.stdout + "\n" + result.stderr).strip()
    with log_path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(f"ASR command model={model} device={device} compute_type={compute_type}\n")
        if debug:
            stream.write(debug + "\n")
    if result.returncode == 0 and output.exists():
        return
    if _looks_like_oom(result.stderr):
        raise AsrError(
            f"ASR 模型 {model} 显存不足。可显式指定 `--fallback-asr-model medium` "
            f"或直接使用 `--asr-model medium`。调试日志: {log_path}"
        )
    raise AsrError(f"ASR 子进程失败（退出码 {result.returncode}）。调试日志: {log_path}")
