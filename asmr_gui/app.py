"""Qt application bootstrap."""

from __future__ import annotations

import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication

from asmr_lrc.environment import configure_cuda_runtime
from asmr_lrc.platform_paths import APP_NAME

from .main_window import MainWindow


def _apply_dark_palette(app: QApplication) -> None:
    """A dark shell, because the lyric view is drawn dark either way."""
    app.setStyle("Fusion")
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, Qt.GlobalColor.black)
    palette.setColor(QPalette.ColorRole.WindowText, Qt.GlobalColor.white)
    palette.setColor(QPalette.ColorRole.Base, Qt.GlobalColor.black)
    palette.setColor(QPalette.ColorRole.AlternateBase, Qt.GlobalColor.darkGray)
    palette.setColor(QPalette.ColorRole.Text, Qt.GlobalColor.white)
    palette.setColor(QPalette.ColorRole.Button, Qt.GlobalColor.darkGray)
    palette.setColor(QPalette.ColorRole.ButtonText, Qt.GlobalColor.white)
    palette.setColor(QPalette.ColorRole.Highlight, Qt.GlobalColor.darkCyan)
    palette.setColor(QPalette.ColorRole.HighlightedText, Qt.GlobalColor.black)
    app.setPalette(palette)


def main(argv: list[str] | None = None) -> int:
    # Pip-installed CUDA libraries must be on the loader path before anything in
    # this process imports ctranslate2, which the probe does.
    configure_cuda_runtime()
    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setOrganizationName("ASMR Translation")
    app.setDesktopFileName("asmr-translation")
    _apply_dark_palette(app)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
