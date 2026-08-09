from pathlib import Path

from .config import SUPPORTED_AUDIO_EXTENSIONS
from .models import ScanItem


def scan_audio(root: Path) -> list[Path]:
    root = root.expanduser().resolve()
    if not root.exists():
        raise FileNotFoundError(f"目录不存在: {root}")
    if not root.is_dir():
        raise NotADirectoryError(f"输入必须是目录: {root}")
    files = (
        path
        for path in root.rglob("*")
        if path.is_file() and path.suffix.casefold() in SUPPORTED_AUDIO_EXTENSIONS
    )
    return sorted(files, key=lambda path: str(path.relative_to(root)).casefold())


def plan_scan(root: Path, *, overwrite: bool) -> list[ScanItem]:
    result: list[ScanItem] = []
    for audio in scan_audio(root):
        lrc = audio.with_suffix(".lrc")
        action = "process" if not lrc.exists() else "overwrite" if overwrite else "skip"
        result.append(ScanItem(audio=audio, lrc=lrc, action=action))
    return result
