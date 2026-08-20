"""Download page: resolve an RJ work, choose files, fetch them.

The GUI only ever holds public metadata -- stable IDs, paths, sizes.  Signed media
URLs are resolved inside the worker thread and never reach a widget, a log line,
or the manifest, which is the same boundary the Win32 build enforced.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..settings import AppSettings
from ..worker import DownloadPlanTask, DownloadTask

_NOTICE = (
    "下载功能只是把你已有权限访问的作品取回本地，"
    "请自行确认来源合法且遵守当地法律与站点条款。\n"
    "本工具不会上传音频，也不会代你绕过任何访问限制。"
)


def _format_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024 or unit == "GiB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} GiB"


class DownloadsPage(QWidget):
    """Fetch a work by RJ number into the download root."""

    status = Signal(str)
    downloaded = Signal(Path)
    notice_acknowledged = Signal()

    def __init__(self, settings_provider, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings: callable[[], AppSettings] = settings_provider
        self._plan_task: DownloadPlanTask | None = None
        self._download_task: DownloadTask | None = None
        self._rj = ""

        self.rj_edit = QLineEdit()
        self.rj_edit.setPlaceholderText("RJ 编号，例如 RJ01234567")
        self.rj_edit.returnPressed.connect(self._fetch)
        self.fetch_button = QPushButton("查询")
        self.fetch_button.clicked.connect(self._fetch)

        self.root_edit = QLineEdit()
        browse = QPushButton("选择…")
        browse.clicked.connect(self._choose_root)

        self.info = QLabel("尚未查询作品。")
        self.info.setWordWrap(True)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["文件", "类型", "大小"])
        self.tree.setRootIsDecorated(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)

        self.audio_only = QCheckBox("只勾选智能推荐的音频")
        self.audio_only.setChecked(True)
        self.audio_only.toggled.connect(self._apply_smart_selection)
        select_all = QPushButton("全选")
        select_all.clicked.connect(lambda: self._set_all(True))
        select_none = QPushButton("全不选")
        select_none.clicked.connect(lambda: self._set_all(False))

        self.download_button = QPushButton("开始下载")
        self.download_button.setEnabled(False)
        self.download_button.clicked.connect(self._start)
        self.cancel_button = QPushButton("取消")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self._cancel)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(140)

        query = QHBoxLayout()
        query.addWidget(QLabel("作品"))
        query.addWidget(self.rj_edit, 1)
        query.addWidget(self.fetch_button)

        target = QHBoxLayout()
        target.addWidget(QLabel("保存到"))
        target.addWidget(self.root_edit, 1)
        target.addWidget(browse)

        selection = QHBoxLayout()
        selection.addWidget(self.audio_only)
        selection.addWidget(select_all)
        selection.addWidget(select_none)
        selection.addStretch(1)
        selection.addWidget(self.download_button)
        selection.addWidget(self.cancel_button)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(_NOTICE))
        layout.addLayout(query)
        layout.addLayout(target)
        layout.addWidget(self.info)
        layout.addWidget(self.tree, 1)
        layout.addLayout(selection)
        layout.addWidget(self.progress)
        layout.addWidget(self.log)

        self._smart_ids: set[str] = set()

    def refresh_from_settings(self) -> None:
        self.root_edit.setText(str(self._settings().download_root_path()))

    # --- metadata ----------------------------------------------------------

    def _choose_root(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "选择下载目录", self.root_edit.text())
        if directory:
            self.root_edit.setText(directory)

    def _fetch(self) -> None:
        rj = self.rj_edit.text().strip()
        if not rj:
            QMessageBox.warning(self, "缺少 RJ 编号", "请输入要下载的作品编号。")
            return
        settings = self._settings()
        if not settings.download_notice_shown:
            reply = QMessageBox.question(
                self,
                "下载须知",
                _NOTICE + "\n\n继续表示你已确认符合上述条件。",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
            self.notice_acknowledged.emit()
        self._rj = rj
        self.tree.clear()
        self.info.setText("正在查询…")
        self.fetch_button.setEnabled(False)
        task = DownloadPlanTask(rj, settings.download_settings(), self)
        task.finished_plan.connect(self._on_plan)
        task.failed.connect(self._on_failed)
        task.finished.connect(lambda: self.fetch_button.setEnabled(True))
        self._plan_task = task
        task.start()

    def _on_plan(self, plan: dict) -> None:
        title = str(plan.get("title", ""))
        circle = str(plan.get("circle", ""))
        total = int(plan.get("total_size", 0))
        self.info.setText(
            f"{plan.get('rj_id', '')} · {title}\n社团: {circle or '未知'} · "
            f"文件 {len(plan.get('files', []))} 个 · 合计 {_format_size(total)}"
        )
        smart = plan.get("smart_selected_ids", [])
        self._smart_ids = {str(value) for value in smart} if isinstance(smart, list) else set()
        self.tree.clear()
        for entry in plan.get("files", []):
            if not isinstance(entry, dict):
                continue
            item = QTreeWidgetItem(
                [
                    str(entry.get("path", "")),
                    str(entry.get("type", "")),
                    _format_size(int(entry.get("size", 0) or 0)),
                ]
            )
            item.setData(0, Qt.ItemDataRole.UserRole, str(entry.get("id", "")))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(0, Qt.CheckState.Unchecked)
            self.tree.addTopLevelItem(item)
        self._apply_smart_selection()
        self.download_button.setEnabled(self.tree.topLevelItemCount() > 0)
        self.status.emit(f"已获取 {plan.get('rj_id', '')} 的文件列表。")

    def _iter_items(self):
        for index in range(self.tree.topLevelItemCount()):
            yield self.tree.topLevelItem(index)

    def _set_all(self, checked: bool) -> None:
        self.audio_only.setChecked(False)
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for item in self._iter_items():
            item.setCheckState(0, state)

    def _apply_smart_selection(self) -> None:
        if not self.audio_only.isChecked():
            return
        for item in self._iter_items():
            file_id = str(item.data(0, Qt.ItemDataRole.UserRole))
            item.setCheckState(
                0,
                Qt.CheckState.Checked if file_id in self._smart_ids else Qt.CheckState.Unchecked,
            )

    # --- transfer ----------------------------------------------------------

    def _selected_ids(self) -> set[str]:
        return {
            str(item.data(0, Qt.ItemDataRole.UserRole))
            for item in self._iter_items()
            if item.checkState(0) == Qt.CheckState.Checked
        }

    def _start(self) -> None:
        selected = self._selected_ids()
        if not selected:
            QMessageBox.warning(self, "未选择文件", "请至少勾选一个文件。")
            return
        raw_root = self.root_edit.text().strip()
        if not raw_root:
            QMessageBox.warning(self, "缺少目录", "请选择下载目录。")
            return
        settings = self._settings()
        self.log.clear()
        self.progress.setValue(0)
        task = DownloadTask(
            self._rj,
            selected,
            Path(raw_root).expanduser(),
            settings.download_settings(),
            self,
        )
        task.event.connect(self._on_event)
        task.finished_download.connect(self._on_done)
        task.failed.connect(self._on_failed)
        task.cancelled.connect(lambda: self.log.append("下载已取消。"))
        task.finished.connect(lambda: self._busy(False))
        self._download_task = task
        self._busy(True)
        task.start()

    def _busy(self, running: bool) -> None:
        self.download_button.setEnabled(not running)
        self.cancel_button.setEnabled(running)
        self.fetch_button.setEnabled(not running)

    def _cancel(self) -> None:
        if self._download_task is not None:
            self._download_task.cancel()

    def _on_event(self, event: dict) -> None:
        name = event.get("event")
        if name == "file":
            self.log.append(f"开始: {event.get('path', '')}")
        elif name == "progress":
            total = int(event.get("total", 0) or 0)
            size = int(event.get("size", 0) or 0)
            if total > 0:
                self.progress.setValue(min(100, int(size / total * 100)))
        elif name == "retry":
            self.log.append(f"重试: {event.get('path', '')}")
        elif name == "complete":
            self.progress.setValue(100)

    def _on_done(self, root: str) -> None:
        self.log.append(f"已保存到 {root}")
        self.status.emit(f"下载完成: {root}")
        self.downloaded.emit(Path(root))

    def _on_failed(self, code: str, message: str) -> None:
        self.info.setText("查询或下载失败。")
        self.log.append(f"错误 [{code}]: {message}")
        self.status.emit(f"下载失败 [{code}]")
