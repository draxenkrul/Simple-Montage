"""Application entry point."""

from __future__ import annotations

import locale
import sys

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication


def _dark_palette() -> QPalette:
    pal = QPalette()
    base, window, text = QColor("#1e1f22"), QColor("#2b2d31"), QColor("#dbdee1")
    pal.setColor(QPalette.ColorRole.Window, window)
    pal.setColor(QPalette.ColorRole.WindowText, text)
    pal.setColor(QPalette.ColorRole.Base, base)
    pal.setColor(QPalette.ColorRole.AlternateBase, window)
    pal.setColor(QPalette.ColorRole.Text, text)
    pal.setColor(QPalette.ColorRole.Button, QColor("#383a40"))
    pal.setColor(QPalette.ColorRole.ButtonText, text)
    pal.setColor(QPalette.ColorRole.ToolTipBase, base)
    pal.setColor(QPalette.ColorRole.ToolTipText, text)
    pal.setColor(QPalette.ColorRole.Highlight, QColor("#3d7bd9"))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText, QPalette.ColorRole.WindowText):
        pal.setColor(QPalette.ColorGroup.Disabled, role, QColor("#6d6f78"))
    return pal


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv if argv is None else argv
    app = QApplication(argv)
    app.setApplicationName("Simple Montage")
    app.setStyle("Fusion")
    app.setPalette(_dark_palette())

    # libmpv requires the C numeric locale; Qt may have changed it.
    locale.setlocale(locale.LC_NUMERIC, "C")

    from simple_montage.main_window import MainWindow  # imports libmpv

    window = MainWindow()
    window.show()
    if len(argv) > 1:
        window.open_file(argv[1])
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
