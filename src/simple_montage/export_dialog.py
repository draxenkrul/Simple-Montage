"""Export options: which part of the timeline, and video and/or sound."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from simple_montage.export import ExportMode
from simple_montage.model import Segment, Timeline
from simple_montage.timecode import format_time


@dataclass
class ExportOptions:
    start: float
    end: float
    mode: ExportMode
    segments: list[Segment]


class ExportDialog(QDialog):
    def __init__(
        self,
        timeline: Timeline,
        has_audio: bool,
        mark_in: float | None,
        mark_out: float | None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Export")
        self._tl = timeline
        dur = timeline.duration
        marks = (
            mark_in if mark_in is not None else 0.0,
            mark_out if mark_out is not None else dur,
        )
        has_marks = mark_in is not None or mark_out is not None

        # ---- range
        self.rb_all = QRadioButton(f"Whole timeline  (0:00:00 – {format_time(dur)})")
        self.rb_marks = QRadioButton(f"In → Out marks  ({format_time(marks[0])} – {format_time(marks[1])})")
        self.rb_marks.setEnabled(has_marks)
        self.rb_custom = QRadioButton("Custom range:")
        self._marks = marks

        self.from_spin = self._seconds_spin(dur)
        self.to_spin = self._seconds_spin(dur)
        self.from_spin.setValue(marks[0] if has_marks else 0.0)
        self.to_spin.setValue(marks[1] if has_marks else dur)

        custom_row = QHBoxLayout()
        custom_row.addWidget(self.rb_custom)
        custom_row.addWidget(QLabel("from"))
        custom_row.addWidget(self.from_spin)
        custom_row.addWidget(QLabel("to"))
        custom_row.addWidget(self.to_spin)
        custom_row.addStretch()

        range_box = QGroupBox("Part of the timeline")
        rl = QVBoxLayout(range_box)
        rl.addWidget(self.rb_all)
        rl.addWidget(self.rb_marks)
        rl.addLayout(custom_row)
        hint = QLabel("Times are positions on the timeline ruler. Parts you cut out "
                      "inside the range stay cut out, and speed changes are applied.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray;")
        rl.addWidget(hint)

        self.range_group = QButtonGroup(self)
        for i, rb in enumerate((self.rb_all, self.rb_marks, self.rb_custom)):
            self.range_group.addButton(rb, i)
        (self.rb_marks if has_marks else self.rb_all).setChecked(True)

        # ---- content
        self.rb_av = QRadioButton("Video with sound  (.mp4)")
        self.rb_video = QRadioButton("Video only, no sound  (.mp4)")
        self.rb_audio = QRadioButton("Sound only  (.mp3, .wav or .m4a)")
        if not has_audio:
            self.rb_av.setText("Video  (.mp4) — this file has no sound")
            self.rb_video.setEnabled(False)
            self.rb_audio.setEnabled(False)
        self.rb_av.setChecked(True)
        self.content_group = QButtonGroup(self)
        for rb in (self.rb_av, self.rb_video, self.rb_audio):
            self.content_group.addButton(rb)
        content_box = QGroupBox("What to export")
        cl = QGridLayout(content_box)
        cl.addWidget(self.rb_av, 0, 0)
        cl.addWidget(self.rb_video, 1, 0)
        cl.addWidget(self.rb_audio, 2, 0)

        # ---- summary + buttons
        self.summary = QLabel()
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Export…")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(range_box)
        layout.addWidget(content_box)
        layout.addWidget(self.summary)
        layout.addWidget(self.buttons)

        self.range_group.idToggled.connect(lambda *_: self._update())
        self.from_spin.valueChanged.connect(self._custom_edited)
        self.to_spin.valueChanged.connect(self._custom_edited)
        self._update()

    @staticmethod
    def _seconds_spin(maximum: float) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(0.0, maximum)
        spin.setDecimals(3)
        spin.setSingleStep(0.5)
        spin.setSuffix(" s")
        spin.setMinimumWidth(110)
        return spin

    def _custom_edited(self) -> None:
        self.rb_custom.setChecked(True)
        self._update()

    def selected_range(self) -> tuple[float, float]:
        if self.rb_marks.isChecked():
            return self._marks
        if self.rb_custom.isChecked():
            return self.from_spin.value(), self.to_spin.value()
        return 0.0, self._tl.duration

    def selected_mode(self) -> ExportMode:
        if self.rb_audio.isChecked():
            return ExportMode.AUDIO_ONLY
        if self.rb_video.isChecked():
            return ExportMode.VIDEO_ONLY
        return ExportMode.VIDEO_AND_AUDIO

    def _update(self) -> None:
        start, end = self.selected_range()
        segments = self._tl.segments_between(start, end) if end > start else []
        ok = bool(segments)
        if end <= start:
            self.summary.setText("⚠ The end must be after the start.")
        elif not ok:
            self.summary.setText("⚠ Everything in this range is cut out — nothing to export.")
        else:
            length = sum(s.output_duration for s in segments)
            self.summary.setText(f"Result: {format_time(length)} long, {len(segments)} segment(s).")
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(ok)

    def options(self) -> ExportOptions:
        start, end = self.selected_range()
        return ExportOptions(start, end, self.selected_mode(), self._tl.segments_between(start, end))
