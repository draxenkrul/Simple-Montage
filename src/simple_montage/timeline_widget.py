"""The timeline strip: shows kept/cut parts of the source, marks and playhead.

The horizontal axis is *source* time. Kept segments are drawn as blocks
(coloured by speed); cut-out parts are hatched.

Overlay clips (videos shown on top) appear on a lane under the main track.

Once the video is split or cut, every kept segment shows a small export (⬇)
button. Right-click opens a menu to export a segment, all segments, or a
cut-out part.

Mouse: click/drag to seek and select a segment, click an overlay to select it
and drag it to move it in time, wheel to zoom, Shift+wheel to scroll.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QCursor, QFont, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QMenu, QToolTip, QWidget

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
    export_segment_requested = Signal(int)
    export_all_requested = Signal()
    export_cut_requested = Signal(float, float)  # a cut-out part: start, end

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
        self.setMouseTracking(True)  # for the export button's hover state
        self._hover_export = -1

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

    def _segment_rect(self, i: int) -> QRectF:
        seg, track = self._tl.segments[i], self._main_track()
        x0, x1 = self._x(seg.start), self._x(seg.end)
        return QRectF(x0, track.top(), max(1.0, x1 - x0), track.height()).adjusted(0.5, 0, -0.5, 0)

    def _export_button_rect(self, i: int) -> QRectF | None:
        """The ⬇ button in a segment's top-right corner (None when hidden)."""
        if not self._tl.has_cuts:
            return None
        rect = self._segment_rect(i)
        if rect.width() < 40 or rect.right() < 0 or rect.left() > self.width():
            return None
        return QRectF(rect.right() - 24, rect.top() + 4, 20, 18)

    def _export_button_at(self, pos: QPointF) -> int:
        for i in range(len(self._tl.segments)):
            r = self._export_button_rect(i)
            if r is not None and r.contains(pos):
                return i
        return -1

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
            self._paint_export_button(p, i)

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

    def _paint_export_button(self, p: QPainter, i: int) -> None:
        r = self._export_button_rect(i)
        if r is None:
            return
        hover = i == self._hover_export
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 230 if hover else 150))
        p.drawRoundedRect(r, 4, 4)
        # download arrow: shaft, head and a base line
        c = QColor("#1e1f22")
        p.setPen(QPen(c, 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        cx, top, bottom = r.center().x(), r.top() + 4, r.bottom() - 6
        p.drawLine(QPointF(cx, top), QPointF(cx, bottom))
        p.drawLine(QPointF(cx - 4, bottom - 4), QPointF(cx, bottom))
        p.drawLine(QPointF(cx + 4, bottom - 4), QPointF(cx, bottom))
        p.drawLine(QPointF(r.left() + 5, r.bottom() - 3), QPointF(r.right() - 5, r.bottom() - 3))

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
            button = self._export_button_at(pos)
            if button >= 0:
                self.segment_clicked.emit(button)
                self.export_segment_requested.emit(button)
                return
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
        hover = self._export_button_at(event.position())
        if hover != self._hover_export:
            self._hover_export = hover
            self.setCursor(Qt.CursorShape.PointingHandCursor if hover >= 0 else Qt.CursorShape.ArrowCursor)
            if hover >= 0:
                QToolTip.showText(QCursor.pos(), "Export this segment…", self)
            else:
                QToolTip.hideText()
            self.update()
        if self._drag_overlay is not None:
            i, offset = self._drag_overlay
            self.overlay_moved.emit(i, self._t(event.position().x()) - offset)
        elif self._dragging:
            self.seek_requested.emit(self._t(event.position().x()))

    def mouseReleaseEvent(self, _event) -> None:  # noqa: N802
        self._dragging = False
        self._drag_overlay = None

    def leaveEvent(self, _event) -> None:  # noqa: N802
        if self._hover_export >= 0:
            self._hover_export = -1
            self.update()

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        if self._tl.duration <= 0:
            return
        t = self._t(event.pos().x())
        menu = QMenu(self)
        index = self._tl.index_at(t)
        if index is not None:
            self.segment_clicked.emit(index)
            seg = self._tl.segments[index]
            menu.addAction(f"Export this segment ({format_time(seg.start)} – {format_time(seg.end)})…",
                           lambda: self.export_segment_requested.emit(index))
        else:
            gap = self._tl.gap_at(t)
            if gap is not None:
                menu.addAction(f"Export this cut-out part, original ({format_time(gap[0])} – "
                               f"{format_time(gap[1])})…", lambda: self.export_cut_requested.emit(*gap))
        if len(self._tl.segments) > 1:
            menu.addAction(f"Export all {len(self._tl.segments)} segments as separate files…",
                           self.export_all_requested.emit)
        if not menu.isEmpty():
            menu.exec(event.globalPos())

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
