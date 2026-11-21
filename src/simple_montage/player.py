"""Video preview widget backed by libmpv.

mpv renders straight into this widget's native window. mpv calls back on its
own thread, so values are passed to the GUI thread through Qt signals.
"""

from __future__ import annotations

import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QWidget

from simple_montage.tools import prepare_mpv_library

prepare_mpv_library()
import mpv  # noqa: E402  (must come after the DLL path is set up)


class MpvPlayer(QWidget):
    position_changed = Signal(float)
    pause_changed = Signal(bool)
    file_loaded = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DontCreateNativeAncestors)
        self.setAttribute(Qt.WidgetAttribute.WA_NativeWindow)
        self.setMinimumSize(320, 180)
        self.setStyleSheet("background-color: black;")

        options = dict(
            wid=str(int(self.winId())),
            pause=True,
            keep_open="yes",          # stay on the last frame instead of closing
            hr_seek="yes",            # every seek is frame-exact
            hr_seek_framedrop="yes",
            osc=False,
            input_default_bindings=False,
            input_vo_keyboard=False,
            idle="yes",
        )
        vo = os.environ.get("SIMPLE_MONTAGE_MPV_VO")  # e.g. "x11" for headless testing
        if vo:
            options["vo"] = vo
        self._mpv = mpv.MPV(**options)
        self._paused = True

        self._mpv.observe_property("time-pos", self._on_time_pos)
        self._mpv.observe_property("pause", self._on_pause)
        self._mpv.event_callback("file-loaded")(lambda _event: self.file_loaded.emit())

    # -------------------------------------------------- mpv thread callbacks
    def _on_time_pos(self, _name: str, value: float | None) -> None:
        if value is not None:
            self.position_changed.emit(float(value))

    def _on_pause(self, _name: str, value: bool | None) -> None:
        self._paused = bool(value)
        self.pause_changed.emit(self._paused)

    # ------------------------------------------------------------- controls
    def load(self, path: str) -> None:
        self._mpv.pause = True
        self._mpv.speed = 1.0
        self._mpv.loadfile(path)

    @property
    def paused(self) -> bool:
        return self._paused

    def play(self) -> None:
        self._mpv.pause = False

    def pause(self) -> None:
        self._mpv.pause = True

    def seek(self, seconds: float) -> None:
        try:
            self._mpv.seek(max(0.0, seconds), reference="absolute", precision="exact")
        except SystemError:
            pass  # no file loaded yet

    def frame_step(self) -> None:
        self._mpv.frame_step()

    def frame_back_step(self) -> None:
        self._mpv.frame_back_step()

    def set_speed(self, speed: float) -> None:
        if abs((self._mpv.speed or 1.0) - speed) > 1e-6:
            self._mpv.speed = speed

    def shutdown(self) -> None:
        self._mpv.terminate()
