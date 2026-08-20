# SPDX-FileCopyrightText: 2025 thiliapr <thiliapr@tutanota.com>
# SPDX-FileContributor: thiliapr <thiliapr@tutanota.com>
# SPDX-License-Identifier: AGPL-3.0-or-later
# Adapted from thiliapr/asmr-one-downloader:
# https://github.com/thiliapr/asmr-one-downloader

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from .control import CancelledError, CancelToken


class DownloadError(RuntimeError):
    """A download request or file transfer failed."""


def _optional_duration(value: object, path: str) -> float | None:
    if value is None:
        return None
    try:
        duration = float(value)
    except (TypeError, ValueError) as exc:
        raise DownloadError(f"音频时长格式错误：{path}") from exc
    if duration < 0:
        raise DownloadError(f"音频时长不能为负数：{path}")
    return duration


@dataclass(frozen=True)
class DownloadConfig:
    endpoint: str = "https://api.asmr-200.com"
    curl_path: str | None = None
    proxy: str | None = None
    connect_timeout: int = 10
    max_retries: int = 5

    def __post_init__(self) -> None:
        if not self.endpoint:
            raise ValueError("下载 endpoint 不能为空")
        parsed = urllib.parse.urlparse(self.endpoint)
        if parsed.scheme.casefold() not in {"http", "https"} or not parsed.netloc:
            raise ValueError("下载 endpoint 必须是 HTTP(S) 地址")
        if self.proxy:
            proxy = urllib.parse.urlparse(self.proxy)
            if (
                proxy.scheme.casefold() not in {"http", "https", "socks5", "socks5h"}
                or not proxy.netloc
            ):
                raise ValueError("下载代理必须是 HTTP(S) 或 SOCKS5 地址")
        if self.connect_timeout <= 0:
            raise ValueError("连接超时必须大于 0")
        if not 1 <= self.max_retries <= 5:
            raise ValueError("单文件重试次数必须在 1 到 5 之间")


@dataclass(frozen=True)
class RemoteFile:
    file_id: str
    path: str
    kind: str
    size: int
    duration: float | None
    digest: str
    media_download_url: str
    media_stream_url: str | None = None

    @property
    def suffix(self) -> str:
        return Path(self.path).suffix.casefold()

    @property
    def is_audio(self) -> bool:
        return self.kind.casefold() == "audio" or self.suffix in {
            ".mp3",
            ".m4a",
            ".aac",
            ".opus",
            ".ogg",
            ".flac",
            ".wav",
            ".wma",
        }

    def public_dict(self) -> dict[str, object]:
        return {
            "id": self.file_id,
            "path": self.path,
            "type": self.kind,
            "size": self.size,
            "duration": self.duration,
        }

    def to_dict(self) -> dict[str, object]:
        value = self.public_dict()
        value.update(
            {
                "digest": self.digest,
                "mediaDownloadUrl": self.media_download_url,
                "mediaStreamUrl": self.media_stream_url,
            }
        )
        return value

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> RemoteFile:
        if not isinstance(value, dict):
            raise DownloadError("文件元数据必须是对象")
        if not str(value.get("id", "")):
            raise DownloadError("文件 ID 不能为空")
        raw_size = value.get("size")
        if isinstance(raw_size, bool) or not isinstance(raw_size, int | float | str):
            raise DownloadError("文件大小缺失或格式错误")
        try:
            size = int(raw_size)
        except (TypeError, ValueError) as exc:
            raise DownloadError("文件大小格式错误") from exc
        if isinstance(raw_size, float) and not raw_size.is_integer():
            raise DownloadError("文件大小格式错误")
        if size < 0:
            raise DownloadError("文件大小不能为负数")
        path = str(value.get("path", ""))
        if not path:
            raise DownloadError("文件路径不能为空")
        return cls(
            file_id=str(value["id"]),
            path=path,
            kind=str(value.get("type", "file")),
            size=size,
            duration=_optional_duration(value.get("duration"), path),
            digest=str(value.get("digest", "")),
            media_download_url=str(value.get("mediaDownloadUrl", "")),
            media_stream_url=(
                None
                if value.get("mediaStreamUrl") in (None, "")
                else str(value["mediaStreamUrl"])
            ),
        )


@dataclass(frozen=True)
class WorkPlan:
    rj_id: str
    source_id: str
    title: str
    circle: str
    release: str
    endpoint: str
    files: tuple[RemoteFile, ...] = field(default_factory=tuple)

    @property
    def total_size(self) -> int:
        return sum(item.size for item in self.files)

    def public_dict(self) -> dict[str, object]:
        return {
            "rj_id": self.rj_id,
            "source_id": self.source_id,
            "title": self.title,
            "circle": self.circle,
            "release": self.release,
            "endpoint": self.endpoint,
            "total_size": self.total_size,
            "files": [item.public_dict() for item in self.files],
        }

    def to_dict(self) -> dict[str, object]:
        value = self.public_dict()
        value["files"] = [item.to_dict() for item in self.files]
        return value

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> WorkPlan:
        if not isinstance(value, dict):
            raise DownloadError("下载计划必须是对象")
        raw_files = value.get("files", [])
        if not isinstance(raw_files, list):
            raise DownloadError("下载计划 files 必须是数组")
        if not str(value.get("rj_id", "")):
            raise DownloadError("下载计划缺少 RJ 编号")
        return cls(
            rj_id=str(value["rj_id"]),
            source_id=str(value.get("source_id", value["rj_id"])),
            title=str(value.get("title", "")),
            circle=str(value.get("circle", "")),
            release=str(value.get("release", "")),
            endpoint=str(value.get("endpoint", "")),
            files=tuple(RemoteFile.from_dict(item) for item in raw_files),
        )


ProgressCallback = Callable[[dict[str, object]], None]

_RJ_ID = re.compile(r"(?i)\b(?:RJ)?\s*(\d{4,10})\b")
_FORBIDDEN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_FORMAT_FOLDERS = {"mp3", "m4a", "aac", "opus", "ogg", "flac", "wav", "wma"}
_AUDIO_PRIORITY = {
    ".mp3": 0,
    ".m4a": 1,
    ".aac": 1,
    ".opus": 2,
    ".ogg": 2,
    ".flac": 3,
    ".wav": 4,
    ".wma": 4,
}


def normalize_rj(value: str) -> str:
    """Return the numeric RJ id accepted by the asmr.one API."""
    if not isinstance(value, str):
        raise ValueError("请输入有效的 RJ 编号或 DLsite 作品链接。")
    text = value.strip()
    match = _RJ_ID.search(text)
    if match is None:
        raise ValueError("请输入有效的 RJ 编号或 DLsite 作品链接。")
    digits = match.group(1)
    # Reject accidental matches inside unrelated words/URLs.
    if text.casefold().startswith(("http://", "https://")):
        parsed = urllib.parse.urlparse(text)
        if parsed.scheme.casefold() not in {"http", "https"} or not parsed.netloc:
            raise ValueError("DLsite 作品链接格式错误。")
        host = parsed.hostname.casefold() if parsed.hostname else ""
        if not (host == "dlsite.com" or host.endswith(".dlsite.com")):
            raise ValueError("只接受 DLsite 作品链接。")
    return str(int(digits))


def _safe_component(value: str, *, limit: int = 120) -> str:
    original = value
    result = _FORBIDDEN.sub("＿", value).strip().rstrip(". ")
    if not result:
        result = "未命名"
    reserved_stem = result.split(".", 1)[0].casefold()
    if reserved_stem in {"con", "prn", "aux", "nul"} or re.fullmatch(
        r"(?i)(com|lpt)[0-9]", reserved_stem
    ):
        result = f"_{result}"
    if len(result) > limit:
        result = result[: max(1, limit - 9)].rstrip(". ")
    if result != original or len(original) > limit:
        digest = hashlib.sha256(original.encode("utf-8")).hexdigest()[:8]
        result = f"{result[: max(1, limit - 9)]}~{digest}"[:limit]
    return result


def safe_relative_path(remote_path: str) -> Path:
    """Convert an API path into a safe Windows-relative path."""
    if not isinstance(remote_path, str) or not remote_path.strip():
        raise DownloadError("远程文件路径不能为空")
    normalized = remote_path.replace("\\", "/")
    raw_parts = normalized.split("/")
    if any(part in {"", ".", ".."} for part in raw_parts):
        raise DownloadError(f"远程文件路径不安全：{remote_path}")
    path = PurePosixPath(normalized)
    if Path(normalized).drive or path.is_absolute():
        raise DownloadError(f"远程文件路径不安全：{remote_path}")
    return Path(*(_safe_component(part) for part in path.parts))


def _request_json(url: str, config: DownloadConfig) -> dict[str, Any] | list[Any]:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.netloc:
        raise DownloadError("请求地址必须是 HTTP(S) 地址")
    request = urllib.request.Request(url, headers={"User-Agent": "asmr-translation/0.3"})
    handlers: list[Any] = []
    if config.proxy:
        handlers.append(urllib.request.ProxyHandler({"http": config.proxy, "https": config.proxy}))
    opener = urllib.request.build_opener(*handlers)
    try:
        with opener.open(request, timeout=config.connect_timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise DownloadError(f"API 请求失败 HTTP {exc.code}: {url}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise DownloadError(f"API 请求失败：{exc}") from exc


def _flatten_children(
    children: Iterable[dict[str, Any]], prefix: PurePosixPath
) -> list[tuple[str, dict[str, Any]]]:
    result: list[tuple[str, dict[str, Any]]] = []
    if not isinstance(children, list):
        raise DownloadError("文件树 children 必须是数组")
    for item in children:
        if not isinstance(item, dict):
            raise DownloadError("文件树项目必须是对象")
        item_type = str(item.get("type", "file"))
        title = str(item.get("title", ""))
        if not title:
            continue
        path = prefix / title
        if item_type == "folder":
            result.extend(_flatten_children(item.get("children", []), path))
        else:
            result.append((str(path), item))
    return result


def fetch_work_plan(rj_value: str, config: DownloadConfig) -> WorkPlan:
    rj_id = normalize_rj(rj_value)
    endpoint = config.endpoint.rstrip("/")
    parsed_endpoint = urllib.parse.urlparse(endpoint)
    if parsed_endpoint.scheme.casefold() not in {"http", "https"} or not parsed_endpoint.netloc:
        raise DownloadError("下载 endpoint 必须是 HTTP(S) 地址")
    work_raw = _request_json(f"{endpoint}/api/workInfo/{rj_id}", config)
    if not isinstance(work_raw, dict):
        raise DownloadError("作品信息返回格式错误。")
    tracks_raw = _request_json(f"{endpoint}/api/tracks/{rj_id}?v=2", config)
    children = tracks_raw.get("children", []) if isinstance(tracks_raw, dict) else tracks_raw
    if not isinstance(children, list):
        raise DownloadError("文件清单返回格式错误。")
    files: list[RemoteFile] = []
    seen_ids: set[str] = set()
    seen_paths: set[str] = set()
    for path, raw in _flatten_children(children, PurePosixPath(".")):
        relative = safe_relative_path(path)
        remote_path = relative.as_posix()
        url = str(raw.get("mediaDownloadUrl", ""))
        if not url:
            continue
        parsed_url = urllib.parse.urlparse(url)
        if parsed_url.scheme.casefold() not in {"http", "https"} or not parsed_url.netloc:
            raise DownloadError(f"媒体地址不是 HTTP(S) 地址：{remote_path}")
        digest = str(raw.get("hash", ""))
        file_id = str(raw.get("id") or hashlib.sha256(
            f"{remote_path}\0{digest}\0{raw.get('size', 0)}".encode()
        ).hexdigest()[:20])
        if not file_id or file_id in seen_ids:
            raise DownloadError(f"文件 ID 缺失或重复：{remote_path}")
        if remote_path.casefold() in seen_paths:
            raise DownloadError(f"清理后的文件路径冲突：{remote_path}")
        seen_ids.add(file_id)
        seen_paths.add(remote_path.casefold())
        raw_size = raw.get("size")
        if isinstance(raw_size, bool) or not isinstance(raw_size, int | float | str):
            raise DownloadError(f"文件大小缺失：{remote_path}")
        try:
            size = int(raw_size)
        except (TypeError, ValueError) as exc:
            raise DownloadError(f"文件大小格式错误：{remote_path}") from exc
        if isinstance(raw_size, float) and not raw_size.is_integer():
            raise DownloadError(f"文件大小格式错误：{remote_path}")
        if size < 0:
            raise DownloadError(f"文件大小不能为负数：{remote_path}")
        stream_url = raw.get("mediaStreamUrl")
        if stream_url not in (None, ""):
            stream_url = str(stream_url)
            parsed_stream = urllib.parse.urlparse(stream_url)
            if parsed_stream.scheme.casefold() not in {"http", "https"} or not parsed_stream.netloc:
                raise DownloadError(f"备用媒体地址不是 HTTP(S) 地址：{remote_path}")
        files.append(
            RemoteFile(
                file_id=file_id,
                path=remote_path,
                kind=str(raw.get("type", "file")),
                size=size,
                duration=_optional_duration(raw.get("duration"), remote_path),
                digest=digest,
                media_download_url=url,
                media_stream_url=stream_url,
            )
        )
    if not files:
        raise DownloadError("作品没有可下载文件。")
    source_id = str(
        work_raw.get("source_id")
        or work_raw.get("sourceId")
        or work_raw.get("id")
        or f"RJ{rj_id}"
    )
    return WorkPlan(
        rj_id=rj_id,
        source_id=source_id,
        title=str(work_raw.get("title") or source_id),
        circle=str(work_raw.get("name") or work_raw.get("circle") or "未知社团"),
        release=str(
            work_raw.get("release")
            or work_raw.get("release_date")
            or work_raw.get("releaseDate")
            or ""
        ),
        endpoint=endpoint,
        files=tuple(files),
    )


def _group_key(item: RemoteFile) -> tuple[str, str]:
    path = PurePosixPath(item.path)
    parents = list(path.parts[:-1])
    if parents and parents[-1].casefold() in _FORMAT_FOLDERS:
        parents.pop()
    stem = path.stem.casefold()
    return "/".join(parents).casefold(), stem


def smart_audio_selection(files: Iterable[RemoteFile]) -> set[str]:
    """Select audio while collapsing only clearly equivalent format variants."""
    audio = [item for item in files if item.is_audio]
    groups: dict[tuple[str, str], list[RemoteFile]] = {}
    for item in audio:
        groups.setdefault(_group_key(item), []).append(item)
    selected: set[str] = set()
    for group in groups.values():
        durations = [item.duration for item in group if item.duration is not None]
        format_dirs = [
            PurePosixPath(item.path).parts[:-1]
            and PurePosixPath(item.path).parts[-2].casefold() in _FORMAT_FOLDERS
            for item in group
        ]
        equivalent = (
            len(group) > 1
            and all(format_dirs)
            and len(durations) == len(group)
            and max(durations) - min(durations) <= 1.5
        )
        if equivalent:
            selected.add(
                min(
                    group,
                    key=lambda item: (_AUDIO_PRIORITY.get(item.suffix, 9), item.size),
                ).file_id
            )
        else:
            selected.update(item.file_id for item in group)
    return selected


def _emit(callback: ProgressCallback | None, event: str, **data: object) -> None:
    if callback is not None:
        callback({"event": event, **data})


def _sleep_with_cancel(seconds: float, token: CancelToken) -> None:
    steps = max(1, int(seconds * 10))
    for _ in range(steps):
        token.raise_if_cancelled()
        time.sleep(seconds / steps)


def _curl_executable(config: DownloadConfig) -> str:
    path = config.curl_path or shutil.which("curl.exe") or shutil.which("curl")
    if not path:
        raise DownloadError("未找到 curl.exe，请在设置中指定路径。")
    if not Path(path).is_file():
        raise DownloadError(f"curl 路径不存在：{path}")
    return str(path)


def _work_directory(root: Path, plan: WorkPlan) -> Path:
    directory = root / _safe_component(f"{plan.title} [{plan.source_id}] [{plan.circle}]")
    root_resolved = root.resolve()
    directory_resolved = directory.resolve()
    if os.path.commonpath((str(root_resolved), str(directory_resolved))) != str(root_resolved):
        raise DownloadError("作品目录越过下载资料库根目录。")
    return directory


def _write_manifest(path: Path, data: dict[str, object]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _curl_transfer(
    remote: RemoteFile,
    target: Path,
    config: DownloadConfig,
    token: CancelToken,
    callback: ProgressCallback | None,
) -> None:
    curl = _curl_executable(config)
    urls = [remote.media_download_url]
    if remote.media_stream_url and remote.media_stream_url not in urls:
        urls.append(remote.media_stream_url)
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(f".{target.name}.part")

    def finalize_part() -> str:
        if target.exists():
            if target.stat().st_size == remote.size:
                part.unlink(missing_ok=True)
                return "skipped"
            raise DownloadError(f"目标文件已存在但大小冲突：{target}")
        os.replace(part, target)
        return "completed"

    if target.exists():
        if target.stat().st_size == remote.size:
            _emit(
                callback,
                "file",
                id=remote.file_id,
                path=remote.path,
                status="skipped",
                size=remote.size,
            )
            return
        raise DownloadError(f"目标文件已存在但大小冲突：{target}")
    if part.exists() and part.stat().st_size > remote.size:
        raise DownloadError(f"临时文件大小超过预期：{part}")
    if part.exists() and part.stat().st_size == remote.size:
        status = finalize_part()
        _emit(
            callback,
            "file",
            id=remote.file_id,
            path=remote.path,
            status=status,
            size=remote.size,
        )
        return
    for url_index, url in enumerate(urls):
        # ``max_retries`` counts retries after the initial request, matching the
        # user-facing setting and the v0.3 contract.
        for attempt in range(1, config.max_retries + 2):
            token.raise_if_cancelled()
            had_partial = part.exists() and part.stat().st_size > 0
            command = [
                curl,
                "--location",
                "--fail",
                "--silent",
                "--show-error",
                "--connect-timeout",
                str(config.connect_timeout),
                "--continue-at",
                "-",
                "--output",
                str(part),
                "--write-out",
                "\n%{http_code}",
            ]
            if config.proxy:
                command.extend(["--proxy", config.proxy])
            command.append(url)
            try:
                process = subprocess.Popen(
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )
            except OSError as exc:
                raise DownloadError(f"无法启动 curl：{exc}") from exc
            while process.poll() is None:
                if token.cancelled:
                    process.terminate()
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=2)
                    raise CancelledError("下载已取消，临时文件已保留。")
                current = part.stat().st_size if part.exists() else 0
                _emit(
                    callback,
                    "progress",
                    id=remote.file_id,
                    path=remote.path,
                    size=current,
                    total=remote.size,
                )
                time.sleep(0.2)
            stdout, stderr = process.communicate()
            status = stdout.strip().splitlines()[-1] if stdout.strip() else "0"
            try:
                status_code = int(status)
            except ValueError:
                status_code = 0
            if process.returncode == 0 and part.exists() and part.stat().st_size == remote.size:
                status = finalize_part()
                _emit(
                    callback,
                    "file",
                    id=remote.file_id,
                    path=remote.path,
                    status=status,
                    size=remote.size,
                )
                return
            if status_code in {401, 403, 404}:
                break
            if status_code == 416 and part.exists() and part.stat().st_size == remote.size:
                status = finalize_part()
                _emit(
                    callback,
                    "file",
                    id=remote.file_id,
                    path=remote.path,
                    status=status,
                    size=remote.size,
                )
                return
            # Some origin servers ignore Range and return the complete object.  A
            # partial file must be restarted once rather than appended to a 200
            # response, otherwise the final size would be invalid.
            if status_code == 200 and had_partial and part.exists():
                part.unlink(missing_ok=True)
                continue
            if attempt <= config.max_retries:
                delay = 2 ** (attempt - 1)
                _emit(
                    callback,
                    "retry",
                    id=remote.file_id,
                    path=remote.path,
                    attempt=attempt,
                    delay=delay,
                    detail=stderr.strip(),
                )
                _sleep_with_cancel(delay, token)
        if url_index + 1 < len(urls):
            continue
    raise DownloadError(f"下载失败：{remote.path}")


def download_plan(
    plan: WorkPlan,
    selected_ids: set[str],
    root: Path,
    config: DownloadConfig,
    *,
    token: CancelToken | None = None,
    callback: ProgressCallback | None = None,
) -> Path:
    cancel = token or CancelToken()
    known_ids = {item.file_id for item in plan.files}
    unknown = selected_ids - known_ids
    if unknown:
        raise DownloadError("选择列表包含未知文件 ID")
    if not selected_ids:
        raise DownloadError("至少选择一个下载文件")
    directory = _work_directory(root, plan)
    directory.mkdir(parents=True, exist_ok=True)
    manifest_path = directory / "download.manifest.json"
    manifest: dict[str, object] = {
        "schema_version": 1,
        "rj_id": plan.rj_id,
        "source_id": plan.source_id,
        "endpoint": plan.endpoint,
        "title": plan.title,
        "circle": plan.circle,
        "files": {},
    }
    if manifest_path.is_file():
        try:
            existing = json.loads(manifest_path.read_text(encoding="utf-8"))
            if (
                isinstance(existing, dict)
                and existing.get("schema_version") == 1
                and str(existing.get("rj_id")) == plan.rj_id
                and str(existing.get("source_id")) == plan.source_id
                and isinstance(existing.get("files"), dict)
            ):
                manifest = existing
        except (OSError, UnicodeError, json.JSONDecodeError):
            # A corrupt manifest is never allowed to block a resumable download;
            # the next atomic write replaces it with a valid one.
            pass
    manifest_files = manifest.get("files")
    if not isinstance(manifest_files, dict):
        manifest_files = {}
        manifest["files"] = manifest_files
    for remote in plan.files:
        entry = manifest_files.setdefault(
            remote.file_id,
            {
                "remote_path": remote.path,
                "local_path": str(safe_relative_path(remote.path)),
                "size": remote.size,
                "selected": remote.file_id in selected_ids,
                "completed": False,
            },
        )
        if isinstance(entry, dict):
            entry["selected"] = remote.file_id in selected_ids
    _write_manifest(manifest_path, manifest)
    total = sum(item.size for item in plan.files if item.file_id in selected_ids)
    completed = 0
    for remote in plan.files:
        if remote.file_id not in selected_ids:
            continue
        cancel.raise_if_cancelled()
        relative = safe_relative_path(remote.path)
        target = directory / relative
        _emit(callback, "file", id=remote.file_id, path=remote.path, status="starting", size=0)

        def transfer_callback(
            event: dict[str, object], base: int = completed, overall: int = total
        ) -> None:
            if event.get("event") == "progress":
                event = dict(event)
                event["size"] = base + int(event.get("size", 0))
                event["total"] = overall
            if callback is not None:
                callback(event)

        _curl_transfer(remote, target, config, cancel, transfer_callback)
        completed += remote.size
        manifest_files[remote.file_id] = {
            "remote_path": remote.path,
            "local_path": str(relative),
            "size": remote.size,
            "selected": True,
            "completed": True,
        }
        _write_manifest(manifest_path, manifest)
        _emit(
            callback,
            "progress",
            id=remote.file_id,
            path=remote.path,
            size=completed,
            total=total,
        )
    _emit(callback, "complete", root=str(directory), source_id=plan.source_id, total=total)
    return directory
