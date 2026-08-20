"""Player page: PCM playback, synchronised lyrics, in-place editing.

The engine is :class:`asmr_lrc.playback.PcmPlayer` rather than QMediaPlayer,
because Qt's GStreamer/WMF backends do not report position precisely enough to
keep a lyric cursor honest at 50 ms.  The position here comes from the audio
callback's own frame index, so the highlighted line matches what is audible.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from asmr_lrc import session
from asmr_lrc.playback import MAX_RATE, MIN_RATE, PcmPlayer, PlaybackError

from ..lyrics_view import Cue, LyricsView
from ..settings import AppSettings
from ..worker import PlaybackPrepareTask

_REFRESH_MS = 50
_RATES = (0.75, 0.9, 1.0, 1.1, 1.25, 1.5, 1.75, 2.0)
_AUDIO_FILTER = "音频 (*.wav *.mp3 *.flac *.m4a *.aac *.ogg *.opus *.wma);;所有文件 (*)"


def _format_time(seconds: float) -> str:
    total = max(0, int(seconds))
    return f"{total // 60:02d}:{total % 60:02d}"


class PlayerPage(QWidget):
    """Synchronised bilingual playback with double-click editing."""

    status = Signal(str)

    def __init__(self, settings_provider, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings: callable[[], AppSettings] = settings_provider
        self._player = PcmPlayer(on_finished=self._on_stream_finished)
        self._audio: Path | None = None
        self._prepare: PlaybackPrepareTask | None = None
        self._seeking = False
        self.setAcceptDrops(True)

        self.open_button = QPushButton("打开音频…")
        self.open_button.clicked.connect(self._choose_audio)
        self.play_button = QPushButton("播放")
        self.play_button.setEnabled(False)
        self.play_button.clicked.connect(self._toggle)
        self.title = QLabel("未载入音频")
        self.title.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        self.rate_box = QComboBox()
        for rate in _RATES:
            self.rate_box.addItem(f"{rate:g}×", rate)
        self.rate_box.setCurrentIndex(_RATES.index(1.0))
        self.rate_box.currentIndexChanged.connect(self._on_rate_changed)

        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(90)
        self.volume.setFixedWidth(120)
        self.volume.valueChanged.connect(lambda value: self._player.set_volume(value / 100))

        self.position = QSlider(Qt.Orientation.Horizontal)
        self.position.setRange(0, 1000)
        self.position.setEnabled(False)
        self.position.sliderPressed.connect(self._begin_seek)
        self.position.sliderReleased.connect(self._end_seek)
        self.clock = QLabel("00:00 / 00:00")

        self.lyrics = LyricsView()
        self.lyrics.seek_requested.connect(self._seek_seconds)
        self.lyrics.edit_requested.connect(self._edit_cue)

        self.save_button = QPushButton("保存人工修改")
        self.save_button.setEnabled(False)
        self.save_button.clicked.connect(self._save_edits)

        top = QHBoxLayout()
        top.addWidget(self.open_button)
        top.addWidget(self.play_button)
        top.addWidget(self.title, 1)
        top.addWidget(QLabel("速度"))
        top.addWidget(self.rate_box)
        top.addWidget(QLabel("音量"))
        top.addWidget(self.volume)

        transport = QHBoxLayout()
        transport.addWidget(self.position, 1)
        transport.addWidget(self.clock)

        bottom = QHBoxLayout()
        bottom.addWidget(QLabel("双击台词可修改译文；单击可跳转。"), 1)
        bottom.addWidget(self.save_button)

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addLayout(transport)
        layout.addWidget(self.lyrics, 1)
        layout.addLayout(bottom)

        self._timer = QTimer(self)
        self._timer.setInterval(_REFRESH_MS)
        self._timer.timeout.connect(self._tick)
        self._edited: dict[str, str] = {}

    # --- loading -----------------------------------------------------------

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802 - Qt naming
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802 - Qt naming
        for url in event.mimeData().urls():
            path = Path(url.toLocalFile())
            if path.is_file():
                self.load(path)
                event.acceptProposedAction()
                return

    def _choose_audio(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "打开音频", "", _AUDIO_FILTER)
        if path:
            self.load(Path(path))

    def load(self, audio: Path) -> None:
        """Prepare the PCM proxy and cues, then hand the proxy to the engine."""
        settings = self._settings()
        self._stop_playback()
        self._audio = audio
        self._edited.clear()
        self.title.setText(f"正在准备 {audio.name}…")
        self.status.emit(f"正在生成播放代理: {audio.name}")
        task = PlaybackPrepareTask(
            audio, settings.cache_root_path(), settings.ffmpeg_path, self
        )
        task.finished_prepare.connect(self._on_prepared)
        task.failed.connect(self._on_failed)
        self._prepare = task
        task.start()

    def _on_prepared(self, result: dict) -> None:
        proxy = Path(str(result["path"]))
        cues = [Cue.from_dict(item) for item in result.get("cues", [])]
        try:
            source = self._player.load(proxy)
        except PlaybackError as exc:
            self._on_failed("PlaybackError", str(exc))
            return
        self.lyrics.set_cues(cues)
        name = Path(str(result.get("source", proxy))).name
        self.title.setText(f"{name} — {source.channels}ch {source.sample_rate} Hz")
        self.play_button.setEnabled(True)
        self.position.setEnabled(True)
        self.save_button.setEnabled(False)
        self.clock.setText(f"00:00 / {_format_time(source.duration)}")
        self._player.set_volume(self.volume.value() / 100)
        self._player.set_rate(float(self.rate_box.currentData()))
        self.status.emit(
            f"已载入 {len(cues)} 条台词。" if cues else "未找到台词缓存或 .lrc，仅音频可播放。"
        )

    def _on_failed(self, code: str, message: str) -> None:
        self.title.setText("载入失败")
        self.status.emit(f"播放准备失败 [{code}]: {message}")
        QMessageBox.warning(self, "无法播放", message)

    # --- transport ---------------------------------------------------------

    def _toggle(self) -> None:
        if not self._player.is_loaded:
            return
        playing = self._player.toggle()
        self.play_button.setText("暂停" if playing else "播放")
        if playing:
            self._timer.start()
        else:
            self._timer.stop()

    def _stop_playback(self) -> None:
        self._timer.stop()
        self._player.stop()
        self.play_button.setText("播放")

    def _on_stream_finished(self) -> None:
        # Fires on the audio thread; only touch Qt through the event loop.
        QTimer.singleShot(0, self._finish_on_gui)

    def _finish_on_gui(self) -> None:
        self._timer.stop()
        self.play_button.setText("播放")
        self.status.emit("播放结束。")

    def _begin_seek(self) -> None:
        self._seeking = True

    def _end_seek(self) -> None:
        self._seeking = False
        duration = self._player.duration
        if duration > 0:
            self._player.seek(self.position.value() / 1000 * duration)
        self._tick()

    def _seek_seconds(self, seconds: float) -> None:
        if self._player.is_loaded:
            self._player.seek(seconds)
            self._tick()

    def _on_rate_changed(self, _index: int) -> None:
        rate = float(self.rate_box.currentData())
        applied = self._player.set_rate(max(MIN_RATE, min(MAX_RATE, rate)))
        self.status.emit(f"播放速度 {applied:g}×（音高不变）")

    def _tick(self) -> None:
        position = self._player.position
        duration = self._player.duration
        self.lyrics.set_position(position)
        if duration > 0 and not self._seeking:
            self.position.setValue(int(position / duration * 1000))
        self.clock.setText(f"{_format_time(position)} / {_format_time(duration)}")

    # --- editing -----------------------------------------------------------

    def _edit_cue(self, index: int) -> None:
        cues = self.lyrics.cues()
        if not (0 <= index < len(cues)):
            return
        cue = cues[index]
        text, accepted = QInputDialog.getText(
            self,
            "修改译文",
            f"原文：{cue.source or '(无)'}",
            text=cue.text,
        )
        if not accepted:
            return
        cleaned = text.strip()
        if not cleaned:
            QMessageBox.warning(self, "译文不能为空", "请填写译文，或取消修改。")
            return
        if cleaned == cue.text:
            return
        self.lyrics.set_cue_text(index, cleaned)
        self._edited[cue.id] = cleaned
        self.save_button.setEnabled(True)
        self.status.emit(f"已修改 {len(self._edited)} 条，尚未保存。")

    def _save_edits(self) -> None:
        if self._audio is None or not self._edited:
            return
        settings = self._settings()
        edits = [{"id": key, "text": value} for key, value in self._edited.items()]
        try:
            result = session.save_edits(self._audio, settings.cache_root_path(), edits)
        except Exception as exc:
            QMessageBox.warning(self, "保存失败", str(exc))
            self.status.emit(f"保存人工修改失败: {exc}")
            return
        self._edited.clear()
        self.save_button.setEnabled(False)
        self.status.emit(f"已写入 {result['lrc']}（备份: {result['backup']}）")

    def shutdown(self) -> None:
        self._timer.stop()
        self._player.close()
