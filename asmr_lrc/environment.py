from __future__ import annotations

import ctypes
import importlib.metadata
import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
import sysconfig
import urllib.error
import urllib.request
from contextlib import suppress
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .platform_paths import describe_platform, is_macos, is_windows

_CUDA_DLL_HANDLES: list[object] = []
_CUDA_PRELOADED: list[object] = []

# pip's nvidia-* wheels use a different layout per platform: DLLs live in bin/
# on Windows and shared objects in lib/ everywhere else.
_CUDA_PACKAGES = ("cublas", "cudnn", "cuda_nvrtc", "cuda_runtime")
# Load order matters on POSIX: cuBLAS needs cuBLASLt, cuDNN needs the runtime.
_CUDA_POSIX_LIBRARIES = (
    "libcudart.so",
    "libcublasLt.so",
    "libcublas.so",
    "libnvrtc.so",
    "libcudnn.so",
)
_CUDA_MACOS_LIBRARIES = ()


def _site_packages() -> Path:
    """Resolve site-packages without assuming the Windows ``Lib`` layout."""
    for key in ("purelib", "platlib"):
        raw = sysconfig.get_paths().get(key)
        if raw and Path(raw).is_dir():
            return Path(raw)
    if is_windows():
        suffix = Path("Lib")
    else:
        suffix = Path("lib") / f"python{sys.version_info.major}.{sys.version_info.minor}"
    return Path(sys.prefix) / suffix / "site-packages"


def cuda_library_directories() -> tuple[Path, ...]:
    """Directories holding pip-installed CUDA libraries, in load order."""
    root = _site_packages() / "nvidia"
    if not root.is_dir():
        return ()
    leaf = "bin" if is_windows() else "lib"
    directories: list[Path] = []
    for package in _CUDA_PACKAGES:
        directory = root / package / leaf
        if directory.is_dir():
            directories.append(directory)
    return tuple(directories)


def _preload_posix_cuda(directories: tuple[Path, ...]) -> None:
    """Load CUDA shared objects by absolute path.

    ``LD_LIBRARY_PATH`` is read by the loader at process start, so appending to
    ``os.environ`` cannot help the process that is already running.  Loading each
    library explicitly puts it in the global namespace, which is what later
    ``dlopen`` calls from CTranslate2 resolve against.
    """
    names = _CUDA_MACOS_LIBRARIES if is_macos() else _CUDA_POSIX_LIBRARIES
    if not names:
        return
    for name in names:
        stem = name.split(".so", 1)[0]
        for directory in directories:
            matches = sorted(directory.glob(f"{stem}.so*")) if not is_macos() else []
            if not matches:
                continue
            with suppress(OSError):
                _CUDA_PRELOADED.append(ctypes.CDLL(str(matches[-1]), mode=ctypes.RTLD_GLOBAL))
                break


def configure_cuda_runtime() -> tuple[str, ...]:
    """Expose pip-installed CUDA libraries to this process and its children.

    Windows needs both ``PATH`` and ``os.add_dll_directory``; POSIX needs the
    libraries preloaded here and ``LD_LIBRARY_PATH`` exported for subprocesses.
    """
    directories = cuda_library_directories()
    if not directories:
        return ()
    configured: list[str] = []
    for directory in directories:
        text = str(directory)
        configured.append(text)
        if is_windows():
            if text not in os.environ.get("PATH", "").split(os.pathsep):
                os.environ["PATH"] = text + os.pathsep + os.environ.get("PATH", "")
            with suppress(AttributeError, OSError):
                _CUDA_DLL_HANDLES.append(os.add_dll_directory(text))
    if not is_windows():
        variable = "DYLD_LIBRARY_PATH" if is_macos() else "LD_LIBRARY_PATH"
        existing = [item for item in os.environ.get(variable, "").split(os.pathsep) if item]
        for text in reversed(configured):
            if text not in existing:
                existing.insert(0, text)
        os.environ[variable] = os.pathsep.join(existing)
        _preload_posix_cuda(directories)
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
    # Must agree with requires-python in pyproject.toml.  The Windows installer
    # still ships the 3.12 embeddable build, but a distro-provided 3.13 is a
    # perfectly good interpreter and must not be reported as broken.
    compatible = (3, 12) <= sys.version_info < (3, 14)
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


def _gpu_check(*, required: bool = True) -> Check:
    check = _run_version(
        [
            "nvidia-smi",
            "--query-gpu=name,memory.total,driver_version",
            "--format=csv,noheader",
        ]
    )
    if check.ok:
        return Check(check.name, True, check.detail, required=required)
    detail = check.detail if required else f"{check.detail}（device={'cpu'} 时可忽略）"
    return Check(check.name, False, detail, required=required)


def _ctranslate_cuda_check(*, required: bool = True) -> Check:
    configure_cuda_runtime()
    if importlib.util.find_spec("ctranslate2") is None:
        return Check("ctranslate2-cuda", False, "CTranslate2 未安装", required=required)
    try:
        import ctranslate2

        count = ctranslate2.get_cuda_device_count()
    except (ImportError, RuntimeError, OSError) as exc:
        return Check("ctranslate2-cuda", False, f"CUDA 探测失败: {exc}", required=required)
    return Check("ctranslate2-cuda", count > 0, f"可见 CUDA 设备: {count}", required=required)


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


def _audio_output_check() -> Check:
    """The player decodes to PCM itself, so it needs a working output device."""
    if importlib.util.find_spec("sounddevice") is None:
        return Check("audio-output", False, "未安装 sounddevice", required=False)
    try:
        import sounddevice

        default = sounddevice.query_devices(kind="output")
    except Exception as exc:  # PortAudio raises its own error types
        return Check("audio-output", False, f"无可用输出设备: {exc}", required=False)
    name = default.get("name", "未知设备") if isinstance(default, dict) else str(default)
    return Check("audio-output", True, str(name), required=False)


def _credential_check() -> Check:
    from .credentials import backend_status

    status = backend_status()
    return Check("credential-store", status.available, status.detail, required=False)


def probe_environment(
    ollama_url: str | None,
    ollama_model: str | None,
    *,
    ffmpeg_path: str = "ffmpeg",
    ollama_path: str = "ollama",
    device: str = "cuda",
) -> dict[str, Any]:
    """Report every dependency the app needs, without installing anything.

    ``device`` decides whether GPU checks are release-blocking: a deliberate CPU
    run must not be reported as a broken environment just because the machine has
    no NVIDIA driver.
    """
    cuda_required = device.casefold().startswith("cuda")
    checks = [
        _python_check(),
        _run_version([ffmpeg_path, "-version"]),
        _gpu_check(required=cuda_required),
        _package_check("faster-whisper"),
        _package_check("ctranslate2"),
        _ctranslate_cuda_check(required=cuda_required),
        _audio_output_check(),
        _credential_check(),
    ]
    if ollama_url is not None and ollama_model is not None:
        checks.extend(
            [
                _run_version([ollama_path, "--version"]),
                *_ollama_check(ollama_url, ollama_model),
            ]
        )
    return {
        "platform": describe_platform(),
        "checks": [asdict(check) for check in checks],
        "ok": all(check.ok for check in checks if check.required),
    }
