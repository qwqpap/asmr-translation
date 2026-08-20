"""Main window: four pages behind a sidebar.

The Win32 build used a tab control plus a manual page host.  Here a
``QListWidget`` drives a ``QStackedWidget``, which gets keyboard navigation and
high-DPI scaling for free on every platform.
"""

from __future__ import annotations

import contextlib
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QSize
from PySide6.QtWidgets import (
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QStackedWidget,
    QStatusBar,
    QWidget,
)

from asmr_lrc import credentials

from .pages import DownloadsPage, PlayerPage, SettingsPage, TasksPage
from .settings import AppSettings, load_settings, save_settings

_PAGES = ("任务", "播放器", "下载", "设置")


class MainWindow(QMainWindow):
    """Owns the settings object every page reads from."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("ASMR Translation")
        self.resize(1180, 780)
        self._settings = load_settings()

        self.tasks = TasksPage(self.settings, self._secrets)
        self.player = PlayerPage(self.settings)
        self.downloads = DownloadsPage(self.settings)
        self.settings_page = SettingsPage(self._settings, self._on_settings_saved)

        self.stack = QStackedWidget()
        for page in (self.tasks, self.player, self.downloads, self.settings_page):
            self.stack.addWidget(page)

        self.nav = QListWidget()
        self.nav.setFixedWidth(148)
        self.nav.setIconSize(QSize(18, 18))
        for name in _PAGES:
            QListWidgetItem(name, self.nav)
        self.nav.setCurrentRow(0)
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)

        central = QWidget()
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.nav)
        layout.addWidget(self.stack, 1)
        self.setCentralWidget(central)

        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("就绪")
        self.player.status.connect(self._show_status)
        self.downloads.status.connect(self._show_status)
        self.downloads.downloaded.connect(self._on_downloaded)
        self.downloads.notice_acknowledged.connect(self._acknowledge_notice)
        self.tasks.finished_root.connect(self._on_pipeline_finished)
        self.downloads.refresh_from_settings()

    # --- shared state ------------------------------------------------------

    def settings(self) -> AppSettings:
        return self._settings

    def _secrets(self) -> dict[str, str]:
        """Read keys at the moment of use, so a rotated key takes effect at once."""
        return {role: credentials.read_secret(role) for role in credentials.roles()}

    def _on_settings_saved(self, settings: AppSettings) -> None:
        self._settings = settings
        self.downloads.refresh_from_settings()
        self._show_status("设置已更新。")

    def _acknowledge_notice(self) -> None:
        if self._settings.download_notice_shown:
            return
        self._settings = replace(self._settings, download_notice_shown=True)
        # Losing the acknowledgement only means the notice shows again.
        with contextlib.suppress(OSError):
            save_settings(self._settings)

    def _show_status(self, message: str) -> None:
        self.statusBar().showMessage(message, 12_000)

    def _on_downloaded(self, root: Path) -> None:
        self.tasks.root_edit.setText(str(root))
        self.nav.setCurrentRow(0)

    def _on_pipeline_finished(self, root: Path) -> None:
        """Offer the first produced track to the player, without switching pages."""
        candidate = root if root.is_file() else next(iter(sorted(root.rglob("*.lrc"))), None)
        if candidate is None:
            return
        audio = candidate if candidate.is_file() and candidate.suffix != ".lrc" else None
        if audio is None:
            for suffix in (".wav", ".mp3", ".flac", ".m4a", ".opus", ".ogg", ".aac", ".wma"):
                sibling = candidate.with_suffix(suffix)
                if sibling.is_file():
                    audio = sibling
                    break
        if audio is not None:
            self._show_status(f"可在播放器中打开 {audio.name}")

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self.player.shutdown()
        super().closeEvent(event)
