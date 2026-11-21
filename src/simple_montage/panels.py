"""Side panels for lighting and overlay settings.

They only show values and emit signals; the main window applies the changes
to the timeline (so undo/redo and the preview stay in one place).
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from simple_montage.model import LIGHT_RANGES, MAX_OVERLAY_SCALE, MIN_OVERLAY_SCALE, Overlay, Segment
from simple_montage.timecode import format_time
from simple_montage.zoom_navigator import ZoomNavigator

LIGHT_LABELS = {
    "brightness": "Brightness",
    "contrast": "Contrast",
    "saturation": "Saturation (colour)",
    "gamma": "Gamma (shadows / midtones)",
}


def _hint(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet("color: gray;")
    return label


def _button(text: str, tooltip: str) -> QPushButton:
    btn = QPushButton(text)
    btn.setToolTip(tooltip)
    btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    return btn


class LightPanel(QWidget):
    """Brightness / contrast / saturation / gamma of the selected segment."""

    light_changed = Signal(dict)   # {name: value} of what changed
    reset_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        grid = QGridLayout()
        self.sliders: dict[str, QSlider] = {}
        self.value_labels: dict[str, QLabel] = {}
        for row, (name, (lo, default, hi)) in enumerate(LIGHT_RANGES.items()):
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(int(round(lo * 100)), int(round(hi * 100)))
            slider.setValue(int(round(default * 100)))
            slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            slider.setToolTip("Double-click the value to reset")
            slider.valueChanged.connect(lambda v, n=name: self._on_slider(n, v))
            value = QLabel()
            value.setMinimumWidth(48)
            self.sliders[name], self.value_labels[name] = slider, value
            grid.addWidget(QLabel(LIGHT_LABELS[name]), row // 2, (row % 2) * 3)
            grid.addWidget(slider, row // 2, (row % 2) * 3 + 1)
            grid.addWidget(value, row // 2, (row % 2) * 3 + 2)
            self._show_value(name, default)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(4, 1)

        self.reset_button = _button("Reset light", "Back to the original look")
        self.reset_button.clicked.connect(self.reset_requested)
        bottom = QHBoxLayout()
        bottom.addWidget(_hint("Changes the selected segment. To change only part of the video, "
                               "split (S) around it first and select that piece."))
        bottom.addStretch()
        bottom.addWidget(self.reset_button)

        layout = QVBoxLayout(self)
        layout.addLayout(grid)
        layout.addLayout(bottom)

    def _show_value(self, name: str, value: float) -> None:
        text = f"{value * 100:+.0f}" if name == "brightness" else f"{value * 100:.0f}%"
        self.value_labels[name].setText(text)

    def _on_slider(self, name: str, slider_value: int) -> None:
        value = slider_value / 100
        self._show_value(name, value)
        self.light_changed.emit({name: value})

    def set_segment(self, seg: Segment | None) -> None:
        self.setEnabled(seg is not None)
        for name, (_lo, default, _hi) in LIGHT_RANGES.items():
            value = getattr(seg, name) if seg is not None else default
            slider = self.sliders[name]
            slider.blockSignals(True)
            slider.setValue(int(round(value * 100)))
            slider.blockSignals(False)
            self._show_value(name, value)
        self.reset_button.setEnabled(seg is not None and seg.has_light)


class OverlayPanel(QWidget):
    """Settings of the selected overlay clip (picture-in-picture)."""

    add_requested = Signal()
    delete_requested = Signal()
    overlay_changed = Signal(dict)   # {field: value} of what changed

    CORNERS = [("↖", 0.03, 0.04), ("↗", 0.97, 0.04), ("◎", 0.5, 0.5), ("↙", 0.03, 0.96), ("↘", 0.97, 0.96)]

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.add_button = _button("➕ Add overlay video…", "Put another video on top of the main one")
        self.add_button.clicked.connect(self.add_requested)
        self.delete_button = _button("🗑 Remove overlay", "Remove the selected overlay (Del)")
        self.delete_button.clicked.connect(self.delete_requested)
        self.name_label = QLabel()

        self.start_spin = self._seconds("Where on the timeline the overlay appears")
        self.in_spin = self._seconds("Which second of the overlay video to start from")
        self.length_spin = self._seconds("How long the overlay is shown (timeline seconds)")
        self.start_spin.valueChanged.connect(lambda v: self.overlay_changed.emit({"start": v}))
        self.in_spin.valueChanged.connect(lambda v: self.overlay_changed.emit({"in_point": v}))
        self.length_spin.valueChanged.connect(lambda v: self.overlay_changed.emit({"length": v}))

        self.size_slider = QSlider(Qt.Orientation.Horizontal)
        self.size_slider.setRange(int(MIN_OVERLAY_SCALE * 100), int(MAX_OVERLAY_SCALE * 100))
        self.size_slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.size_slider.setToolTip("Width of the overlay compared to the main picture")
        self.size_label = QLabel()
        self.size_label.setMinimumWidth(40)
        self.size_slider.valueChanged.connect(self._on_size)

        self.sound_check = QCheckBox("Mix in its sound")
        self.sound_check.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.sound_check.toggled.connect(lambda on: self.overlay_changed.emit({"sound": on}))

        self.map = ZoomNavigator()
        self.map.setToolTip("Drag the box to place the overlay. Double-click to centre.")
        self.map.view_changed.connect(lambda x, y: self.overlay_changed.emit({"pos_x": x, "pos_y": y}))
        self.corner_buttons = []
        corners = QGridLayout()
        for i, (text, x, y) in enumerate(self.CORNERS):
            btn = _button(text, "Move the overlay here")
            btn.setFixedWidth(30)
            btn.clicked.connect(lambda _=False, x=x, y=y: self.overlay_changed.emit({"pos_x": x, "pos_y": y}))
            self.corner_buttons.append(btn)
            corners.addWidget(btn, *[(0, 0), (0, 2), (1, 1), (2, 0), (2, 2)][i])

        top = QHBoxLayout()
        top.addWidget(self.add_button)
        top.addWidget(self.name_label, stretch=1)
        top.addWidget(self.delete_button)

        timing = QHBoxLayout()
        for text, w in (("Show at", self.start_spin), ("for", self.length_spin),
                        ("starting from its second", self.in_spin)):
            timing.addWidget(QLabel(text))
            timing.addWidget(w)
        timing.addStretch()

        look = QHBoxLayout()
        look.addWidget(QLabel("Size"))
        look.addWidget(self.size_slider, stretch=1)
        look.addWidget(self.size_label)
        look.addSpacing(12)
        look.addWidget(self.sound_check)

        left = QVBoxLayout()
        left.addLayout(top)
        left.addLayout(timing)
        left.addLayout(look)
        self.hint = _hint("Tip: drag the purple block on the timeline to move the overlay in time.")
        left.addWidget(self.hint)

        layout = QHBoxLayout(self)
        layout.addLayout(left, stretch=1)
        layout.addLayout(corners)
        layout.addWidget(self.map)
        self.set_overlay(None, 16 / 9, 0.0)

    @staticmethod
    def _seconds(tooltip: str) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setDecimals(2)
        spin.setSingleStep(0.5)
        spin.setSuffix(" s")
        spin.setKeyboardTracking(False)
        spin.setToolTip(tooltip)
        spin.setMinimumWidth(90)
        return spin

    def _on_size(self, value: int) -> None:
        self.size_label.setText(f"{value}%")
        self.overlay_changed.emit({"scale": value / 100})

    def set_overlay(self, ov: Overlay | None, main_aspect: float, main_duration: float) -> None:
        widgets = [self.delete_button, self.start_spin, self.in_spin, self.length_spin,
                   self.size_slider, self.sound_check, self.map, *self.corner_buttons]
        for w in widgets:
            w.setEnabled(ov is not None)
            w.blockSignals(True)
        self.map.set_aspect(main_aspect)
        if ov is None:
            self.name_label.setText("Select a purple overlay block on the timeline, or add one.")
            self.map.set_box(1.0, 1.0, 0.5, 0.5)
            self.size_label.setText("")
        else:
            self.name_label.setText(
                f"<b>{Path(ov.path).name}</b> — shown {format_time(ov.start)} – {format_time(ov.end)}")
            self.start_spin.setRange(0.0, main_duration)
            self.start_spin.setValue(ov.start)
            self.in_spin.setRange(0.0, ov.file_duration)
            self.in_spin.setValue(ov.in_point)
            self.length_spin.setRange(0.0, ov.file_duration)
            self.length_spin.setValue(ov.length)
            self.size_slider.setValue(int(round(ov.scale * 100)))
            self.size_label.setText(f"{int(round(ov.scale * 100))}%")
            self.sound_check.setChecked(ov.sound)
            self.sound_check.setEnabled(ov.has_audio)
            self.sound_check.setText("Mix in its sound" if ov.has_audio else "No sound in this video")
            height = ov.scale * main_aspect / ov.aspect
            self.map.set_box(ov.scale, height, ov.pos_x, ov.pos_y)
        for w in widgets:
            w.blockSignals(False)
