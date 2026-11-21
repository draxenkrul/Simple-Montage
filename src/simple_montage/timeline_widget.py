"""The timeline strip: shows kept/cut parts of the source, marks and playhead.

The horizontal axis is *source* time. Kept segments are drawn as blocks
(coloured by speed); cut-out parts are hatched.

Overlay clips (videos shown on top) appear on a lane under the main track.

Mouse: click/drag to seek and select a segment, click an overlay to select it
and drag it to move it in time, wheel to zoom, Shift+wheel to scroll.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QWidget

from simple_montage.model import Timeline
from simple_montage.timecode import format_speed, format_time

RULER_H = 22
TICK_STEPS = [0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600]

COL_BG = QColor("#1e1f22")
COL_RULER = QColor("#2b2d31")
COL_TEXT = QColor("#b5bac1")
COL_CUT = QColor("#3a2a2a")
COL_SEG = QColor("#3d7bd9")
COL_SEG_FAST = QColor("#d98a3d")
COL_SEG_SLOW = QColor("#3dbf8a")
COL_SELECTED = QColor("#ffffff")
COL_MARK = QColor("#f2c94c")
COL_PLAYHEAD = QColor("#ff4d4d")
COL_OVERLAY = QColor("#9b59d0")
LANE_H = 24


class TimelineWidget(QWidget):
    seek_requested = Signal(float)
    segment_clicked = Signal(int)  # -1 when clicking a cut-out area
    overlay_clicked = Signal(int)
    overlay_moved = Signal(int, float)  # index, new start time

    def __init__(self, timeline: Timeline, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tl = timeline
        self._playhead = 0.0
        self._mark_in: float | None = None
        self._mark_out: float | None = None
        self._selected = -1
        self._selected_overlay = -1
        self._drag_overlay: tuple[int, float] | None = None  # (index, grab offset)
        self._view_start = 0.0
        self._view_len = 0.0  # 0 means "show everything"
        self._dragging = False
        self.setMinimumHeight(110)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    # ------------------------------------------------------------ public API
    def reset_view(self) -> None:
        self._view_start = 0.0
        self._view_len = self._tl.duration
        self.update()

    def set_playhead(self, t: float) -> None:
        self._playhead = t
        # keep the playhead visible while playing on a zoomed-in timeline
        if self._view_len and not (self._view_start <= t <= self._view_start + self._view_len):
            self._set_view(t - self._view_len * 0.1, self._view_len)
        self.update()

    def set_marks(self, mark_in: float | None, mark_out: float | None) -> None:
        self._mark_in, self._mark_out = mark_in, mark_out
        self.update()

    def set_selected(self, index: int) -> None:
        self._selected = index
        self.update()

    def set_selected_overlay(self, index: int) -> None:
        self._selected_overlay = index
        self.update()

    # ---------------------------------------------------------------- layout
    def _main_track(self) -> QRectF:
        bottom = self.height() - 8 - ((LANE_H + 6) if self._tl.overlays else 0)
        return QRectF(0, RULER_H + 8, self.width(), bottom - RULER_H - 8)

    def _lane(self) -> QRectF:
        return QRectF(0, self.height() - 8 - LANE_H, self.width(), LANE_H)

    def _overlay_rect(self, i: int) -> QRectF:
        ov, lane = self._tl.overlays[i], self._lane()
        x0, x1 = self._x(ov.start), self._x(ov.end)
        return QRectF(x0, lane.top(), max(4.0, x1 - x0), lane.height())

    # ----------------------------------------------------------- coordinates
    def _x(self, t: float) -> float:
        if self._view_len <= 0:
            return 0.0
        return (t - self._view_start) / self._view_len * self.width()

    def _t(self, x: float) -> float:
        if self._view_len <= 0:
            return 0.0
        t = self._view_start + x / max(1, self.width()) * self._view_len
        return min(max(t, 0.0), self._tl.duration)

    def _set_view(self, start: float, length: float) -> None:
        dur = self._tl.duration
        min_len = min(dur, self._tl.frame_duration * 20) if dur else 0.0
        length = min(max(length, min_len), dur)
        start = min(max(start, 0.0), max(0.0, dur - length))
        self._view_start, self._view_len = start, length

    # -------------------------------------------------------------- painting
    def paintEvent(self, _event) -> None:  # noqa: N802 (Qt naming)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, COL_BG)
        p.fillRect(0, 0, w, RULER_H, COL_RULER)
        if self._tl.duration <= 0:
            p.setPen(COL_TEXT)
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                       "Open a video (Ctrl+O) or drop a file here")
            return
        if self._view_len <= 0:
            self.reset_view()

        self._paint_ruler(p, w)
        track = self._main_track()

        # cut-out background across the whole source
        start_x, end_x = self._x(0), self._x(self._tl.duration)
        p.fillRect(QRectF(start_x, track.top(), end_x - start_x, track.height()),
                   QBrush(COL_CUT, Qt.BrushStyle.BDiagPattern))

        font = QFont(self.font())
        font.setPointSizeF(max(7.0, font.pointSizeF() - 1))
        p.setFont(font)
        for i, seg in enumerate(self._tl.segments):
            x0, x1 = self._x(seg.start), self._x(seg.end)
            if x1 < 0 or x0 > w:
                continue
            rect = QRectF(x0, track.top(), max(1.0, x1 - x0), track.height()).adjusted(0.5, 0, -0.5, 0)
            color = COL_SEG if seg.speed == 1 else (COL_SEG_FAST if seg.speed > 1 else COL_SEG_SLOW)
            p.setBrush(color)
            p.setPen(QPen(COL_SELECTED, 2) if i == self._selected else QPen(color.darker(140), 1))
            p.drawRoundedRect(rect, 3, 3)
            tags = []
            if seg.speed != 1:
                tags.append(format_speed(seg.speed))
            if seg.zoom > 1:
                tags.append(f"🔍{format_speed(round(seg.zoom, 2))}")
            if seg.has_light:
                tags.append("☀")
            if tags and rect.width() > 30:
                p.setPen(Qt.GlobalColor.white)
                p.drawText(rect, Qt.AlignmentFlag.AlignCenter, "  ".join(tags))

        self._paint_overlays(p)
        self._paint_marks(p, track)

        x = self._x(self._playhead)
        p.setPen(QPen(COL_PLAYHEAD, 2))
        p.drawLine(int(x), 0, int(x), h)
        p.setBrush(COL_PLAYHEAD)
        p.drawPolygon(QPolygonF([QPointF(x - 6, 0), QPointF(x + 6, 0), QPointF(x, 8)]))

    def _paint_ruler(self, p: QPainter, w: int) -> None:
        step = TICK_STEPS[-1]
        for candidate in TICK_STEPS:
            if candidate / self._view_len * w >= 80:
                step = candidate
                break
        p.setPen(COL_TEXT)
        t = (self._view_start // step) * step
        end = self._view_start + self._view_len
        while t <= end + step:
            x = self._x(t)
            p.drawLine(int(x), RULER_H - 6, int(x), RULER_H)
            label = format_time(t, millis=step < 1)
            if label.startswith("0:"):
                label = label[2:]
            p.drawText(int(x) + 3, RULER_H - 8, label)
            t += step

    def _paint_overlays(self, p: QPainter) -> None:
        for i, ov in enumerate(self._tl.overlays):
            rect = self._overlay_rect(i)
            p.setBrush(COL_OVERLAY)
            selected = i == self._selected_overlay
            p.setPen(QPen(COL_SELECTED, 2) if selected else QPen(COL_OVERLAY.darker(150), 1))
            p.drawRoundedRect(rect, 3, 3)
            if rect.width() > 40:
                p.setPen(Qt.GlobalColor.white)
                name = Path(ov.path).name + ("  🔊" if ov.sound else "")
                text = p.fontMetrics().elidedText(name, Qt.TextElideMode.ElideMiddle, int(rect.width()) - 8)
                p.drawText(rect.adjusted(4, 0, -4, 0), Qt.AlignmentFlag.AlignVCenter, text)

    def _paint_marks(self, p: QPainter, track: QRectF) -> None:
        if self._mark_in is not None and self._mark_out is not None:
            x0, x1 = self._x(self._mark_in), self._x(self._mark_out)
            shade = QColor(COL_MARK)
            shade.setAlpha(50)
            p.fillRect(QRectF(x0, 0, x1 - x0, self.height()), shade)
        p.setPen(QPen(COL_MARK, 2))
        for t, label in ((self._mark_in, "I"), (self._mark_out, "O")):
            if t is not None:
                x = self._x(t)
                p.drawLine(int(x), RULER_H, int(x), self.height())
                p.drawText(int(x) + 3, int(track.bottom()) + 2, label)

    # ----------------------------------------------------------------- mouse
    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self._tl.duration > 0:
            pos = event.position()
            for i in range(len(self._tl.overlays) - 1, -1, -1):  # topmost first
                if self._overlay_rect(i).contains(pos):
                    self._drag_overlay = (i, self._t(pos.x()) - self._tl.overlays[i].start)
                    self.overlay_clicked.emit(i)
                    return
            self._dragging = True
            t = self._t(event.position().x())
            index = self._tl.index_at(t)
            self.segment_clicked.emit(-1 if index is None else index)
            self.seek_requested.emit(t)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag_overlay is not None:
            i, offset = self._drag_overlay
            self.overlay_moved.emit(i, self._t(event.position().x()) - offset)
        elif self._dragging:
            self.seek_requested.emit(self._t(event.position().x()))

    def mouseReleaseEvent(self, _event) -> None:  # noqa: N802
        self._dragging = False
        self._drag_overlay = None

    def wheelEvent(self, event) -> None:  # noqa: N802
        if self._tl.duration <= 0:
            return
        delta = event.angleDelta()
        shift = event.modifiers() & Qt.KeyboardModifier.ShiftModifier
        if delta.x() or shift:  # scroll
            amount = (delta.x() or delta.y()) / 120
            self._set_view(self._view_start - amount * self._view_len * 0.1, self._view_len)
        else:  # zoom around the cursor
            anchor = self._t(event.position().x())
            factor = 0.8 ** (delta.y() / 120)
            new_len = self._view_len * factor
            frac = (anchor - self._view_start) / self._view_len if self._view_len else 0
            self._set_view(anchor - frac * new_len, new_len)
        self.update()
