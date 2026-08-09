from dataclasses import dataclass, field


@dataclass(slots=True)
class BatchReport:
    total: int = 0
    succeeded: int = 0
    skipped: int = 0
    cache_hits: int = 0
    failed: int = 0
    transcribed: int = 0
    translated: int = 0
    failures: list[tuple[str, str]] = field(default_factory=list)

    @property
    def exit_code(self) -> int:
        attempted = self.total - self.skipped
        if self.failed == 0:
            return 0
        if attempted > self.failed:
            return 1
        return 2

    def add_failure(self, path: str, reason: str) -> None:
        self.failed += 1
        self.failures.append((path, reason))

    def summary(self) -> str:
        return (
            f"总计={self.total} 成功={self.succeeded} 跳过={self.skipped} "
            f"缓存命中={self.cache_hits} 转写={self.transcribed} "
            f"翻译={self.translated} 失败={self.failed}"
        )
