"""Bilingual lyric view drawn with QPainter.

This replaces the Direct2D/DirectWrite view.  The behaviour it must preserve:
the active line is larger and highlighted, the Japanese source sits under the
Chinese translation, lines flagged by the pipeline are visibly marked, a click
seeks and a double-click starts editing.

Scrolling is centred on the active line and animated toward its target rather
than snapped, because a hard jump on every cue makes long ASMR tracks unreadable.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QWheelEvent,
)
from PySide6.QtWidgets import QWidget

_MARGIN = 24.0
_LINE_GAP = 6.0
_BLOCK_GAP = 18.0
_SCROLL_SMOOTHING = 0.22


@dataclass(slots=True)
class Cue:
    id: str
    start: float
    end: float | None
    source: str
    text: str
    flags: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> Cue:
        raw_end = data.get("end")
        raw_flags = data.get("flags")
        return cls(
            id=str(data.get("id", "")),
            start=float(data.get("start", 0.0) or 0.0),
            end=None if raw_end in (None, "") else float(raw_end),  # type: ignore[arg-type]
            source=str(data.get("source", "")),
            text=str(data.get("text", "")),
            flags=tuple(str(flag) for flag in raw_flags) if isinstance(raw_flags, list) else (),
        )


@dataclass(slots=True)
class _Row:
    index: int
    top: float
    height: float


class LyricsView(QWidget):
    """Scrolling bilingual lyric list with seek-on-click."""

    seek_requested = Signal(float)
    edit_requested = Signal(int)
    selection_changed = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(220)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAutoFillBackground(False)
        self._cues: list[Cue] = []
        self._active = -1
        self._selected = -1
        self._rows: list[_Row] = []
        self._scroll = 0.0
        self._target_scroll = 0.0
        self._manual_scroll = False
        self._content_height = 0.0
        self._normal = QFont(self.font())
        self._normal.setPointSizeF(max(10.5, self.font().pointSizeF()))
        self._active_font = QFont(self._normal)
        self._active_font.setPointSizeF(self._normal.pointSizeF() * 1.35)
        self._active_font.setBold(True)
        self._source_font = QFont(self._normal)
        self._source_font.setPointSizeF(self._normal.pointSizeF() * 0.92)
        self._colors = {
            "background": QColor(18, 18, 22),
            "primary": QColor(232, 232, 238),
            "secondary": QColor(150, 152, 162),
            "active": QColor(126, 200, 255),
            "issue": QColor(240, 168, 96),
            "selection": QColor(255, 255, 255, 22),
        }

    # --- data --------------------------------------------------------------

    def cues(self) -> list[Cue]:
        return self._cues

    def set_cues(self, cues: list[Cue]) -> None:
        self._cues = list(cues)
        self._active = -1
        self._selected = -1
        self._scroll = 0.0
        self._target_scroll = 0.0
        self._manual_scroll = False
        self._layout_rows()
        self.update()

    def set_cue_text(self, index: int, text: str) -> None:
        if 0 <= index < len(self._cues):
            self._cues[index].text = text
            self._layout_rows()
            self.update()

    def selected_index(self) -> int:
        return self._selected

    def active_index(self) -> int:
        return self._active

    def index_at_time(self, seconds: float) -> int:
        """Last cue whose start is at or before ``seconds``."""
        low, high = 0, len(self._cues) - 1
        found = -1
        while low <= high:
            middle = (low + high) // 2
            if self._cues[middle].start <= seconds:
                found = middle
                low = middle + 1
            else:
                high = middle - 1
        return found

    def set_position(self, seconds: float) -> None:
        index = self.index_at_time(seconds)
        if index != self._active:
            self._active = index
            self._manual_scroll = False
            self._layout_rows()
            self._recentre()
        self._step_scroll()

    # --- layout ------------------------------------------------------------

    def _row_height(self, index: int) -> float:
        font = self._active_font if index == self._active else self._normal
        height = QFontMetricsF(font).height()
        if self._cues[index].source:
            height += QFontMetricsF(self._source_font).height() + _LINE_GAP
        return height

    def _layout_rows(self) -> None:
        self._rows = []
        offset = _MARGIN
        for index in range(len(self._cues)):
            height = self._row_height(index)
            self._rows.append(_Row(index, offset, height))
            offset += height + _BLOCK_GAP
        self._content_height = offset + _MARGIN

    def _max_scroll(self) -> float:
        return max(0.0, self._content_height - self.height())

    def _recentre(self) -> None:
        if not (0 <= self._active < len(self._rows)):
            return
        row = self._rows[self._active]
        centre = row.top + row.height / 2 - self.height() / 2
        self._target_scroll = min(max(0.0, centre), self._max_scroll())

    def _step_scroll(self) -> None:
        if self._manual_scroll:
            return
        delta = self._target_scroll - self._scroll
        if abs(delta) < 0.5:
            if self._scroll != self._target_scroll:
                self._scroll = self._target_scroll
                self.update()
            return
        self._scroll += delta * _SCROLL_SMOOTHING
        self.update()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().resizeEvent(event)
        self._layout_rows()
        self._recentre()

    # --- painting ----------------------------------------------------------

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 - Qt naming
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        painter.fillRect(self.rect(), self._colors["background"])
        if not self._cues:
            painter.setPen(self._colors["secondary"])
            painter.setFont(self._normal)
            painter.drawText(
                self.rect(),
                int(Qt.AlignmentFlag.AlignCenter),
                "尚未载入歌词。请在任务页处理音频，或直接打开已有的 .lrc。",
            )
            return
        width = self.width() - _MARGIN * 2
        for row in self._rows:
            top = row.top - self._scroll
            if top + row.height < 0 or top > self.height():
                continue
            cue = self._cues[row.index]
            is_active = row.index == self._active
            if row.index == self._selected:
                painter.fillRect(
                    QRectF(_MARGIN / 2, top - 6, self.width() - _MARGIN, row.height + 12),
                    self._colors["selection"],
                )
            font = self._active_font if is_active else self._normal
            if cue.flags:
                colour = self._colors["issue"]
            elif is_active:
                colour = self._colors["active"]
            else:
                colour = self._colors["primary"]
            painter.setFont(font)
            painter.setPen(colour)
            metrics = QFontMetricsF(font)
            painter.drawText(QPointF(_MARGIN, top + metrics.ascent()), cue.text)
            if cue.source:
                painter.setFont(self._source_font)
                painter.setPen(self._colors["secondary"])
                source_metrics = QFontMetricsF(self._source_font)
                baseline = top + metrics.height() + _LINE_GAP + source_metrics.ascent()
                source_rect = QRectF(
                    _MARGIN,
                    baseline - source_metrics.ascent(),
                    width,
                    source_metrics.height(),
                )
                painter.drawText(
                    source_rect,
                    int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                    cue.source,
                )

    # --- interaction -------------------------------------------------------

    def _hit_test(self, y: float) -> int:
        position = y + self._scroll
        for row in self._rows:
            if row.top - _BLOCK_GAP / 2 <= position <= row.top + row.height + _BLOCK_GAP / 2:
                return row.index
        return -1

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt naming
        index = self._hit_test(event.position().y())
        if index < 0:
            return
        self._selected = index
        self.selection_changed.emit(index)
        self.update()
        if event.button() == Qt.MouseButton.LeftButton:
            self.seek_requested.emit(self._cues[index].start)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt naming
        index = self._hit_test(event.position().y())
        if index >= 0:
            self._selected = index
            self.edit_requested.emit(index)

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802 - Qt naming
        delta = event.angleDelta().y()
        if delta == 0:
            return
        # Manual scrolling wins until the next cue change, so the user can read
        # ahead without the auto-centre yanking the view back.
        self._manual_scroll = True
        self._scroll = min(max(0.0, self._scroll - delta * 0.6), self._max_scroll())
        self.update()

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self._selected >= 0:
            self.edit_requested.emit(self._selected)
            return
        super().keyPressEvent(event)
