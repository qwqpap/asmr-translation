from __future__ import annotations

import importlib.metadata
import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from contextlib import suppress
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

_CUDA_DLL_HANDLES: list[object] = []


def configure_cuda_runtime() -> tuple[str, ...]:
    """Expose pip-installed CUDA DLL directories to Windows child processes."""
    if os.name != "nt":
        return ()
    site_packages = Path(sys.prefix) / "Lib" / "site-packages"
    relative_directories = (
        Path("nvidia") / "cublas" / "bin",
        Path("nvidia") / "cudnn" / "bin",
        Path("nvidia") / "cuda_nvrtc" / "bin",
    )
    configured: list[str] = []
    for relative in relative_directories:
        directory = site_packages / relative
        if not directory.is_dir():
            continue
        text = str(directory)
        if text not in os.environ.get("PATH", "").split(os.pathsep):
            os.environ["PATH"] = text + os.pathsep + os.environ.get("PATH", "")
        with suppress(AttributeError, OSError):
            _CUDA_DLL_HANDLES.append(os.add_dll_directory(text))
        configured.append(text)
    return tuple(configured)


@dataclass(frozen=True, slots=True)
class Check:
    name: str
    ok: bool
    detail: str
    required: bool = True


def _run_version(command: list[str]) -> Check:
    name = command[0]
    executable = shutil.which(name)
    if executable is None:
        return Check(name=name, ok=False, detail="未在 PATH 中找到")
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return Check(name=name, ok=False, detail=str(exc))
    output = (result.stdout or result.stderr).strip().splitlines()
    detail = output[0] if output else f"退出码 {result.returncode}"
    return Check(name=name, ok=result.returncode == 0, detail=detail)


def _python_check() -> Check:
    compatible = sys.version_info[:2] == (3, 12)
    detail = f"{platform.python_version()} ({sys.executable})"
    return Check("python", compatible, detail)


def _package_check(package: str) -> Check:
    module = package.replace("-", "_")
    if importlib.util.find_spec(module) is None:
        return Check(package, False, "未安装")
    try:
        version = importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        version = "版本未知"
    return Check(package, True, version)


def _ctranslate_cuda_check() -> Check:
    configure_cuda_runtime()
    if importlib.util.find_spec("ctranslate2") is None:
        return Check("ctranslate2-cuda", False, "CTranslate2 未安装")
    try:
        import ctranslate2

        count = ctranslate2.get_cuda_device_count()
    except (ImportError, RuntimeError, OSError) as exc:
        return Check("ctranslate2-cuda", False, f"CUDA 探测失败: {exc}")
    return Check("ctranslate2-cuda", count > 0, f"可见 CUDA 设备: {count}")


def ollama_request(
    base_url: str,
    endpoint: str,
    *,
    payload: dict[str, Any] | None = None,
    timeout: float = 10,
) -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}{endpoint}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="GET" if data is None else "POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        result = json.loads(response.read().decode("utf-8"))
    if not isinstance(result, dict):
        raise ValueError("Ollama 返回的根节点不是 JSON 对象")
    return result


def ollama_models(base_url: str) -> set[str]:
    data = ollama_request(base_url, "/api/tags")
    return {
        str(item.get("name", ""))
        for item in data.get("models", [])
        if isinstance(item, dict) and item.get("name")
    }


def ollama_running_models(base_url: str) -> set[str]:
    data = ollama_request(base_url, "/api/ps")
    return {
        str(item.get("name", ""))
        for item in data.get("models", [])
        if isinstance(item, dict) and item.get("name")
    }


def _ollama_check(base_url: str, model: str) -> list[Check]:
    try:
        models = ollama_models(base_url)
    except (OSError, ValueError, json.JSONDecodeError, urllib.error.URLError) as exc:
        return [
            Check("ollama-service", False, f"无法连接 {base_url}: {exc}"),
            Check("ollama-model", False, f"无法检查模型 {model}"),
        ]
    return [
        Check("ollama-service", True, base_url),
        Check(
            "ollama-model",
            model in models,
            f"{model} {'可用' if model in models else '未安装'}",
        ),
    ]


def probe_environment(ollama_url: str, ollama_model: str) -> dict[str, Any]:
    checks = [
        _python_check(),
        _run_version(["ffmpeg", "-version"]),
        _run_version(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,driver_version",
                "--format=csv,noheader",
            ]
        ),
        _run_version(["ollama", "--version"]),
        _package_check("faster-whisper"),
        _package_check("ctranslate2"),
        _ctranslate_cuda_check(),
        *_ollama_check(ollama_url, ollama_model),
    ]
    return {
        "platform": platform.platform(),
        "checks": [asdict(check) for check in checks],
        "ok": all(check.ok for check in checks if check.required),
    }
