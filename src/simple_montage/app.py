"""Application entry point."""

from __future__ import annotations

import locale
import sys
from pathlib import Path

from PySide6.QtGui import QColor, QIcon, QPalette
from PySide6.QtWidgets import QApplication, QMessageBox

from simple_montage import __version__
from simple_montage.tools import find_ffmpeg, find_ffprobe

ICON_PATH = Path(__file__).resolve().parent / "resources" / "icon.png"


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


def diagnostics() -> tuple[bool, list[str]]:
    """Check that everything the app needs is installed. Returns (ok, report lines)."""
    import platform

    import PySide6

    lines = [
        f"Simple Montage {__version__}",
        f"Python {platform.python_version()} ({platform.architecture()[0]}) at {sys.executable}",
        f"PySide6 {PySide6.__version__}",
    ]
    ok = True
    for name, path in (("ffmpeg", find_ffmpeg()), ("ffprobe", find_ffprobe())):
        lines.append(f"{name}: {path or 'NOT FOUND - put ' + name + '.exe into the bin folder'}")
        ok = ok and path is not None
    try:
        locale.setlocale(locale.LC_NUMERIC, "C")
        from simple_montage import main_window  # noqa: F401  (imports libmpv)
        from simple_montage.player import mpv

        player = mpv.MPV(vo="null", ao="null")
        lines.append(f"libmpv: {player.mpv_version}")
        player.terminate()
    except Exception as exc:  # report anything, this is a diagnostic
        ok = False
        lines.append(f"libmpv: NOT LOADED - put libmpv-2.dll into the bin folder ({exc})")
    lines.append("RESULT: " + ("OK - everything is in place" if ok else "PROBLEM - see above"))
    return ok, lines


def smoke_test(report_path: str) -> int:
    """Write the diagnostics to a file (used to verify a packaged .exe)."""
    ok, lines = diagnostics()
    Path(report_path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv if argv is None else argv
    if len(argv) > 1 and argv[1] == "--check":
        ok, lines = diagnostics()
        print("\n".join(lines))
        return 0 if ok else 1
    if len(argv) > 1 and argv[1] == "--smoke-test":
        return smoke_test(argv[2] if len(argv) > 2 else "smoke-test.txt")

    app = QApplication(argv)
    app.setApplicationName("Simple Montage")
    app.setApplicationVersion(__version__)
    app.setWindowIcon(QIcon(str(ICON_PATH)))
    app.setStyle("Fusion")
    app.setPalette(_dark_palette())

    # libmpv requires the C numeric locale; Qt may have changed it.
    locale.setlocale(locale.LC_NUMERIC, "C")

    try:
        from simple_montage.main_window import MainWindow  # imports libmpv
    except OSError as exc:
        QMessageBox.critical(
            None, "Simple Montage",
            "The video player library (libmpv-2.dll) could not be loaded.\n\n"
            "Put libmpv-2.dll into the 'bin' folder next to the app.\n\n"
            f"Details: {exc}",
        )
        return 1

    window = MainWindow()
    window.show()
    if len(argv) > 1:
        window.open_file(argv[1])
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
