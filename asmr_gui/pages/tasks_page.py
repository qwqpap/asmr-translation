"""Task page: pick a folder, check the environment, run the pipeline.

Drag-and-drop replaces the Win32 ``WM_DROPFILES`` handler.  The consent prompt is
a hard gate: the pipeline blocks in its worker thread until the user answers,
because sending transcript text to an external API is not something to infer.
"""

from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QTimer, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..settings import AppSettings
from ..worker import PipelineTask, ProbeTask

_AUDIO_SUFFIXES = {".wav", ".mp3", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wma"}


class TasksPage(QWidget):
    """Runs :func:`asmr_lrc.pipeline.run_pipeline` against a chosen root."""

    request_settings = Signal()
    finished_root = Signal(Path)

    def __init__(self, settings_provider, secrets_provider, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings: callable[[], AppSettings] = settings_provider
        self._secrets = secrets_provider
        self._task: PipelineTask | None = None
        self._probe: ProbeTask | None = None
        self.setAcceptDrops(True)

        self.root_edit = QLineEdit()
        self.root_edit.setPlaceholderText("拖入文件夹或音频文件，或点击“选择…”")
        browse = QPushButton("选择…")
        browse.clicked.connect(self._choose_root)

        self.dry_run = QCheckBox("仅试运行（不写入 LRC）")
        self.transcribe_only = QCheckBox("仅转写")
        self.translate_only = QCheckBox("仅翻译（使用已有转写缓存）")
        self.overwrite = QCheckBox("覆盖已存在的 .lrc")
        self.keep_model = QCheckBox("结束后保留模型常驻显存")

        self.start_button = QPushButton("开始处理")
        self.start_button.clicked.connect(self._start)
        self.cancel_button = QPushButton("取消")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self._cancel)
        self.probe_button = QPushButton("环境自检")
        self.probe_button.clicked.connect(self._run_probe)

        self.stage_label = QLabel("就绪")
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)

        source_box = QGroupBox("输入")
        source_layout = QHBoxLayout(source_box)
        source_layout.addWidget(QLabel("目录/文件"))
        source_layout.addWidget(self.root_edit, 1)
        source_layout.addWidget(browse)

        options_box = QGroupBox("选项")
        options_layout = QGridLayout(options_box)
        for index, widget in enumerate(
            (
                self.dry_run,
                self.transcribe_only,
                self.translate_only,
                self.overwrite,
                self.keep_model,
            )
        ):
            options_layout.addWidget(widget, index // 3, index % 3)

        actions = QHBoxLayout()
        actions.addWidget(self.start_button)
        actions.addWidget(self.cancel_button)
        actions.addWidget(self.probe_button)
        actions.addStretch(1)

        layout = QVBoxLayout(self)
        layout.addWidget(source_box)
        layout.addWidget(options_box)
        layout.addLayout(actions)
        layout.addWidget(self.stage_label)
        layout.addWidget(self.progress)
        layout.addWidget(self.log, 1)

    # --- drag and drop -----------------------------------------------------

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802 - Qt naming
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802 - Qt naming
        for url in event.mimeData().urls():
            path = Path(url.toLocalFile())
            if path.is_dir() or path.suffix.casefold() in _AUDIO_SUFFIXES:
                self.root_edit.setText(str(path))
                event.acceptProposedAction()
                return

    # --- actions -----------------------------------------------------------

    def _choose_root(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "选择音频目录", self.root_edit.text())
        if directory:
            self.root_edit.setText(directory)

    def append_log(self, message: str) -> None:
        self.log.append(message)

    def _busy(self, running: bool) -> None:
        self.start_button.setEnabled(not running)
        self.cancel_button.setEnabled(running)
        self.probe_button.setEnabled(not running)

    def _run_probe(self) -> None:
        settings = self._settings()
        self.append_log("开始环境自检…")
        task = ProbeTask(settings.worker_config(self._secrets()), self)
        task.finished_probe.connect(self._on_probe)
        task.failed.connect(self._on_failed)
        task.finished.connect(lambda: self._busy(False))
        self._probe = task
        self._busy(True)
        task.start()

    def _on_probe(self, result: dict) -> None:
        self.append_log(f"平台: {result.get('platform', '未知')}")
        for check in result.get("checks", []):
            mark = "OK " if check.get("ok") else ("!! " if check.get("required", True) else "-- ")
            self.append_log(f"{mark}{check.get('name')}: {check.get('detail')}")
        for check in result.get("provider_checks", []):
            mark = "OK " if check.get("ok") else "!! "
            line = f"{mark}{check.get('kind')} / {check.get('model')}"
            if not check.get("ok"):
                line += f": {check.get('detail', '')}"
            if check.get("install_command"):
                line += f"\n    请手动执行: {check['install_command']}"
            self.append_log(line)
        if result.get("ok"):
            self.append_log("自检通过。")
        else:
            self.append_log("自检未通过，请先修复上面标记的项目。")

    def _start(self) -> None:
        raw = self.root_edit.text().strip()
        if not raw:
            QMessageBox.warning(self, "缺少输入", "请先选择音频目录或文件。")
            return
        root = Path(raw).expanduser()
        if not root.exists():
            QMessageBox.warning(self, "路径不存在", f"找不到 {root}")
            return
        settings = self._settings()
        self.log.clear()
        self.progress.setValue(0)
        self.stage_label.setText("准备中…")
        task = PipelineTask(
            root,
            settings.worker_config(self._secrets(), overwrite=self.overwrite.isChecked()),
            dry_run=self.dry_run.isChecked(),
            transcribe_only=self.transcribe_only.isChecked(),
            translate_only=self.translate_only.isChecked(),
            keep_model=self.keep_model.isChecked(),
            consent=self._ask_consent,
            parent=self,
        )
        task.event.connect(self._on_event)
        task.finished_run.connect(self._on_finished)
        task.failed.connect(self._on_failed)
        task.cancelled.connect(self._on_cancelled)
        task.finished.connect(lambda: self._busy(False))
        self._task = task
        self._busy(True)
        task.start()

    def _cancel(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self.stage_label.setText("正在取消…")

    def _ask_consent(self, estimated_characters: int) -> bool:
        """Ask on the GUI thread while the pipeline thread waits for the answer.

        The dialog cannot be created off the GUI thread, and the pipeline needs a
        decision before it sends anything, so the worker blocks on an event that
        the GUI thread sets.  Cancellation releases the wait too, otherwise a
        cancelled run would hang here forever.
        """
        decided = threading.Event()
        answer = False

        def prompt() -> None:
            nonlocal answer
            reply = QMessageBox.question(
                self,
                "外部 API 授权",
                "本次翻译将向外部 API 发送约 "
                f"{estimated_characters} 个字符的转写文本。\n"
                "音频文件不会上传。是否继续？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            answer = reply == QMessageBox.StandardButton.Yes
            decided.set()

        QTimer.singleShot(0, prompt)
        task = self._task
        while not decided.wait(0.1):
            if task is not None and task.token.cancelled:
                return False
        return answer

    # --- events ------------------------------------------------------------

    def _on_event(self, event: dict) -> None:
        name = event.get("event")
        if name == "log":
            message = str(event.get("message", ""))
            if message:
                self.append_log(message)
        elif name == "plan":
            items = event.get("items", [])
            self.append_log(f"计划处理 {len(items)} 个文件。")
        elif name == "phase":
            labels = {"asr": "转写", "context": "语境分析", "translation": "翻译"}
            phase = labels.get(str(event.get("phase")), str(event.get("phase")))
            current = int(event.get("current", 0))
            total = max(1, int(event.get("total", 1)))
            self.stage_label.setText(f"{phase} {current}/{total}")
            self.progress.setValue(int(current / total * 100))
        elif name == "batch":
            stage = "初译" if event.get("stage") == "draft" else "审校"
            current = int(event.get("current", 0))
            total = max(1, int(event.get("total", 1)))
            self.stage_label.setText(f"{stage}批次 {current}/{total}")
        elif name == "result":
            report = event.get("report", {})
            if isinstance(report, dict):
                self.append_log(
                    "完成: 总计={total} 成功={succeeded} 跳过={skipped} 失败={failed}".format(
                        total=report.get("total", 0),
                        succeeded=report.get("succeeded", 0),
                        skipped=report.get("skipped", 0),
                        failed=report.get("failed", 0),
                    )
                )

    def _on_finished(self, exit_code: int, _summary: dict) -> None:
        self.progress.setValue(100)
        self.stage_label.setText("完成" if exit_code == 0 else f"完成，退出码 {exit_code}")
        raw = self.root_edit.text().strip()
        if raw:
            self.finished_root.emit(Path(raw).expanduser())

    def _on_cancelled(self) -> None:
        self.stage_label.setText("已取消")
        self.append_log("任务已取消。")

    def _on_failed(self, code: str, message: str) -> None:
        self.stage_label.setText("失败")
        self.append_log(f"错误 [{code}]: {message}")
