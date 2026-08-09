from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .errors import CacheError
from .models import SourceIdentity


def source_identity(path: Path, *, chunk_size: int = 1024 * 1024) -> SourceIdentity:
    absolute = path.resolve()
    stat = absolute.stat()
    digest = hashlib.sha256()
    with absolute.open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return SourceIdentity(
        path=str(absolute),
        size=stat.st_size,
        modified_ns=stat.st_mtime_ns,
        fingerprint=digest.hexdigest(),
    )


def cache_key(source: SourceIdentity) -> str:
    path_hash = hashlib.sha256(source.path.casefold().encode("utf-8")).hexdigest()[:16]
    return f"{path_hash}-{source.fingerprint[:16]}"


def cache_directory(root: Path, source: SourceIdentity) -> Path:
    return root / cache_key(source)


def same_source(left: SourceIdentity, right: SourceIdentity) -> bool:
    return (
        Path(left.path) == Path(right.path)
        and left.size == right.size
        and left.modified_ns == right.modified_ns
        and left.fingerprint == right.fingerprint
    )


def atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def load_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as stream:
            value = json.load(stream)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CacheError(f"缓存损坏或不可读: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise CacheError(f"缓存根节点必须是 JSON 对象: {path}")
    return value


def load_validated[T](path: Path, parser: Callable[[dict[str, Any]], T]) -> T:
    try:
        return parser(load_json(path))
    except (KeyError, TypeError, ValueError) as exc:
        raise CacheError(f"缓存结构无效: {path}: {exc}") from exc


def quarantine_corrupt(path: Path) -> Path:
    index = 1
    while True:
        target = path.with_name(f"{path.name}.corrupt-{index}")
        if not target.exists():
            path.replace(target)
            return target
        index += 1


def quarantine_stale(path: Path) -> Path:
    index = 1
    while True:
        target = path.with_name(f"{path.name}.stale-{index}")
        if not target.exists():
            path.replace(target)
            return target
        index += 1
