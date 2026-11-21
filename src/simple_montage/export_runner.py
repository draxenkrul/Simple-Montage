"""Runs an FFmpeg export in the background and reports progress."""

from __future__ import annotations

from collections import deque

from PySide6.QtCore import QObject, QProcess, Signal

from simple_montage.export import parse_progress_line


class ExportRunner(QObject):
    progress = Signal(float)        # 0.0 .. 1.0
    finished = Signal(bool, str)    # success, error message

    def __init__(self, args: list[str], output_duration: float, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._args = args
        self._total = max(output_duration, 1e-3)
        self._stderr_tail: deque[str] = deque(maxlen=15)
        self._cancelled = False
        self._proc = QProcess(self)
        self._proc.readyReadStandardOutput.connect(self._read_stdout)
        self._proc.readyReadStandardError.connect(self._read_stderr)
        self._proc.finished.connect(self._on_finished)
        self._proc.errorOccurred.connect(self._on_error)

    def start(self) -> None:
        self._proc.start(self._args[0], self._args[1:])

    def cancel(self) -> None:
        self._cancelled = True
        self._proc.kill()

    def _read_stdout(self) -> None:
        text = bytes(self._proc.readAllStandardOutput()).decode("utf-8", "replace")
        for line in text.splitlines():
            seconds = parse_progress_line(line)
            if seconds is not None:
                self.progress.emit(min(1.0, seconds / self._total))

    def _read_stderr(self) -> None:
        text = bytes(self._proc.readAllStandardError()).decode("utf-8", "replace")
        self._stderr_tail.extend(line for line in text.splitlines() if line.strip())

    def _on_finished(self, exit_code: int, _status) -> None:
        if self._cancelled:
            self.finished.emit(False, "Export cancelled.")
        elif exit_code == 0:
            self.progress.emit(1.0)
            self.finished.emit(True, "")
        else:
            self.finished.emit(False, "\n".join(self._stderr_tail) or f"FFmpeg exited with code {exit_code}.")

    def _on_error(self, error) -> None:
        if error == QProcess.ProcessError.FailedToStart:
            self.finished.emit(False, f"Could not start FFmpeg:\n{self._args[0]}")
