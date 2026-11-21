"""Small map of the video frame for choosing the visible area when zoomed.

The outer rectangle is the whole frame, the highlighted box is the part that
is shown. Drag the box (or click anywhere) to move it; double-click to centre.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget


class ZoomNavigator(QWidget):
    view_changed = Signal(float, float)  # pan_x, pan_y in 0..1

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._zoom, self._pan_x, self._pan_y = 1.0, 0.5, 0.5
        self._aspect = 16 / 9
        self.setFixedSize(128, 72)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setToolTip("Drag the box to choose which part of the picture is shown.\n"
                        "Double-click to centre.")

    def set_aspect(self, aspect: float) -> None:
        if aspect > 0:
            self._aspect = aspect
            self.update()

    def set_view(self, zoom: float, pan_x: float, pan_y: float) -> None:
        self._zoom, self._pan_x, self._pan_y = zoom, pan_x, pan_y
        self.update()

    # ------------------------------------------------------------ geometry
    def _frame(self) -> QRectF:
        w, h = self.width() - 4, self.height() - 4
        if w / h > self._aspect:
            fw, fh = h * self._aspect, h
        else:
            fw, fh = w, w / self._aspect
        return QRectF((self.width() - fw) / 2, (self.height() - fh) / 2, fw, fh)

    def _box(self, frame: QRectF) -> QRectF:
        bw, bh = frame.width() / self._zoom, frame.height() / self._zoom
        return QRectF(frame.left() + (frame.width() - bw) * self._pan_x,
                      frame.top() + (frame.height() - bh) * self._pan_y, bw, bh)

    # ------------------------------------------------------------ painting
    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        frame = self._frame()
        enabled = self.isEnabled()
        p.fillRect(frame, QColor("#111214"))
        p.setPen(QPen(QColor("#3a3c42"), 1))
        for i in (1, 2):  # rule-of-thirds guides
            x = frame.left() + frame.width() * i / 3
            y = frame.top() + frame.height() * i / 3
            p.drawLine(int(x), int(frame.top()), int(x), int(frame.bottom()))
            p.drawLine(int(frame.left()), int(y), int(frame.right()), int(y))
        p.setPen(QPen(QColor("#6d6f78"), 1))
        p.drawRect(frame)
        if self._zoom > 1.0 + 1e-9:
            box = self._box(frame)
            fill = QColor("#3d7bd9" if enabled else "#555555")
            fill.setAlpha(110)
            p.fillRect(box, fill)
            p.setPen(QPen(QColor("#ffffff" if enabled else "#888888"), 1.5))
            p.drawRect(box)

    # --------------------------------------------------------------- mouse
    def _pan_from(self, x: float, y: float) -> tuple[float, float]:
        frame = self._frame()
        box = self._box(frame)

        def axis(pos: float, start: float, size: float, box_size: float) -> float:
            free = size - box_size
            if free <= 0:
                return 0.5
            return min(max((pos - start - box_size / 2) / free, 0.0), 1.0)

        return (axis(x, frame.left(), frame.width(), box.width()),
                axis(y, frame.top(), frame.height(), box.height()))

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self._zoom > 1.0:
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self.view_changed.emit(*self._pan_from(event.position().x(), event.position().y()))

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if event.buttons() & Qt.MouseButton.LeftButton and self._zoom > 1.0:
            self.view_changed.emit(*self._pan_from(event.position().x(), event.position().y()))

    def mouseReleaseEvent(self, _event) -> None:  # noqa: N802
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    def mouseDoubleClickEvent(self, _event) -> None:  # noqa: N802
        self.view_changed.emit(0.5, 0.5)
