from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from .errors import AsmrLrcError


class CancelledError(AsmrLrcError):
    """The user cancelled an in-progress pipeline."""


class CancelToken:
    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise CancelledError("任务已取消")


EventCallback = Callable[[dict[str, Any]], None]


def emit(callback: EventCallback | None, event: str, **data: Any) -> None:
    if callback is not None:
        callback({"event": event, **data})
