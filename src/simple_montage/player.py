"""Video preview widget backed by libmpv.

mpv renders straight into this widget's native window. mpv calls back on its
own thread, so values are passed to the GUI thread through Qt signals.
"""

from __future__ import annotations

import math
import os

from collections.abc import Callable

from PySide6.QtCore import QRect, Qt, Signal
from PySide6.QtWidgets import QWidget

from simple_montage.model import Overlay

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
        self._light_filter = ""
        self.last_position = 0.0

        self._mpv.observe_property("time-pos", self._on_time_pos)
        self._mpv.observe_property("pause", self._on_pause)
        self._mpv.event_callback("file-loaded")(lambda _event: self.file_loaded.emit())

    # -------------------------------------------------- mpv thread callbacks
    def _on_time_pos(self, _name: str, value: float | None) -> None:
        if value is not None:
            self.last_position = float(value)
            self.position_changed.emit(float(value))

    def _on_pause(self, _name: str, value: bool | None) -> None:
        self._paused = bool(value)
        self.pause_changed.emit(self._paused)

    # ------------------------------------------------------------- controls
    def load(self, path: str) -> None:
        self._mpv.pause = True
        self._mpv.speed = 1.0
        self._light_filter = ""
        self._mpv.loadfile(path)

    def load_clips(self, clips: list[tuple[str, float]]) -> None:
        """Play several files back to back as one timeline.

        ``clips`` are ``(path, duration)`` pairs. Uses mpv's EDL playlist so
        seeking and playback run seamlessly across clips; the durations are
        given explicitly so mpv's timeline matches ours exactly.
        """
        if len(clips) == 1:
            self.load(clips[0][0])
            return
        parts = [f"%{len(path.encode('utf-8'))}%{path},0,{duration:.6f}" for path, duration in clips]
        self.load("edl://" + ";".join(parts))

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
        percent = max(0, min(100, int(percent)))
        if getattr(self, "_volume", None) != percent:
            self._volume = percent
            self._mpv.volume = percent

    def set_muted(self, muted: bool) -> None:
        if getattr(self, "_muted", None) != bool(muted):
            self._muted = bool(muted)
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

    def set_light(self, light: dict[str, float] | None) -> None:
        """Preview lighting with the same FFmpeg ``eq`` filter the export uses.

        ``None`` (or default values) removes the filter so normal playback
        costs nothing extra.
        """
        spec = ""
        if light:
            spec = "eq=" + ":".join(f"{k}={v:.4f}" for k, v in light.items())
        if spec == self._light_filter:
            return
        self._light_filter = spec
        try:
            self._mpv.vf = spec
        except (SystemError, RuntimeError):
            pass

    @property
    def speed(self) -> float:
        return float(self._mpv.speed or 1.0)

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
        self.on_layout: Callable[[], None] | None = None  # called after the player moves
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
        if self.on_layout:
            self.on_layout()


class OverlayPreview:
    """Shows an overlay clip on top of the main preview, kept in sync with it.

    Uses a second, small mpv player placed over the main one at the overlay's
    size and position. It is created the first time an overlay is shown.
    """

    def __init__(self, frame: VideoFrame, resync: Callable[[], None]) -> None:
        self.frame = frame
        self.resync = resync          # asks the window to call update() again
        self.player: MpvPlayer | None = None
        self._path: str | None = None
        self._overlay: Overlay | None = None
        frame.on_layout = self._place

    def _ensure_player(self) -> MpvPlayer:
        if self.player is None:
            self.player = MpvPlayer(self.frame)
            self.player.set_muted(True)
            self.player.hide()
            self.player.file_loaded.connect(self.resync)
        return self.player

    def _place(self) -> None:
        ov, p = self._overlay, self.player
        if ov is None or p is None:
            return
        main = self.frame.player.geometry()
        ow = max(8, int(main.width() * ov.scale))
        oh = max(8, int(ow / ov.aspect))
        x = main.x() + int((main.width() - ow) * ov.pos_x)
        y = main.y() + int((main.height() - oh) * ov.pos_y)
        p.setGeometry(QRect(x, y, ow, oh))

    def hide(self) -> None:
        if self.player is not None and self.player.isVisible():
            self.player.pause()
            self.player.hide()
        self._overlay = None

    def update(self, overlay: Overlay | None, t: float, playing: bool, speed: float,
               volume: int, muted: bool) -> None:
        """Show ``overlay`` as it looks at main source time ``t`` (or hide)."""
        if overlay is None or not overlay.active_at(t):
            self.hide()
            return
        p = self._ensure_player()
        self._overlay = overlay
        if overlay.path != self._path:
            self._path = overlay.path
            p.load(overlay.path)
            p.last_position = -1.0
        self._place()
        if not p.isVisible():
            p.show()
            p.raise_()
        p.set_volume(volume)
        p.set_muted(muted or not overlay.sound)

        target = overlay.file_time(t)
        drift = abs(p.last_position - target)
        if playing:
            p.set_speed(speed)
            if p.paused or drift > 0.25:
                p.seek(target)
                p.play()
        else:
            if not p.paused:
                p.pause()
            if drift > 0.02:
                p.seek(target)

    def shutdown(self) -> None:
        if self.player is not None:
            self.player.shutdown()
