"""Video preview widget backed by libmpv.

mpv renders straight into this widget's native window. mpv calls back on its
own thread, so values are passed to the GUI thread through Qt signals.
"""

from __future__ import annotations

import math
import os

from PySide6.QtCore import QRect, Qt, Signal
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
        self.setMinimumSize(160, 90)
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
        self._view = (1.0, 0.5, 0.5)

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

    def set_volume(self, percent: int) -> None:
        """Preview loudness, 0-100 (does not affect the export)."""
        self._mpv.volume = max(0, min(100, int(percent)))

    def set_muted(self, muted: bool) -> None:
        self._mpv.mute = bool(muted)

    def set_view(self, zoom: float, pan_x: float, pan_y: float) -> None:
        """Show a zoomed part of the picture, the same area the export will keep.

        ``pan_x``/``pan_y`` (0..1) choose the visible area: 0 = left/top edge,
        0.5 = centre, 1 = right/bottom edge. Uses mpv's display zoom, so it is
        instant and does not interrupt playback.
        """
        view = (zoom, pan_x, pan_y)
        if view == self._view:
            return
        self._view = view
        # mpv pans in fractions of the zoomed picture; positive moves it right/down
        shift = 1.0 - 1.0 / zoom
        self._mpv.video_zoom = math.log2(zoom)
        self._mpv.video_pan_x = -(pan_x - 0.5) * shift
        self._mpv.video_pan_y = -(pan_y - 0.5) * shift

    def shutdown(self) -> None:
        self._mpv.terminate()


class VideoFrame(QWidget):
    """Black area that keeps the player at the video's aspect ratio.

    With the player exactly as wide/tall as the picture, a zoomed preview is
    clipped at the picture's edges - matching what the export keeps - instead
    of spilling into letterbox bars.
    """

    def __init__(self, player: MpvPlayer, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.player = player
        player.setParent(self)
        self._aspect = 16 / 9
        self.setStyleSheet("background-color: black;")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.setMinimumSize(320, 180)

    def set_aspect(self, aspect: float) -> None:
        if aspect > 0:
            self._aspect = aspect
            self._layout_player()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._layout_player()

    def _layout_player(self) -> None:
        w, h = self.width(), self.height()
        if w / max(h, 1) > self._aspect:
            pw, ph = int(h * self._aspect), h
        else:
            pw, ph = w, int(w / self._aspect)
        self.player.setGeometry(QRect((w - pw) // 2, (h - ph) // 2, pw, ph))
