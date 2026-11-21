"""Main application window: preview, transport controls, timeline, segment panel."""

from __future__ import annotations

import os
import time
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from simple_montage.export import AUDIO_FORMATS, ExportMode, build_ffmpeg_args
from simple_montage.export_dialog import ExportDialog
from simple_montage.export_runner import ExportRunner
from simple_montage.model import MAX_SPEED, MIN_SPEED, Timeline
from simple_montage.player import MpvPlayer
from simple_montage.probe import MediaInfo, ProbeError, probe
from simple_montage.timecode import format_speed, format_time
from simple_montage.timeline_widget import TimelineWidget
from simple_montage.tools import find_ffmpeg, find_ffprobe

VIDEO_FILTER = "Videos (*.mp4 *.mov *.mkv *.avi *.webm *.m4v *.mts *.m2ts *.wmv *.flv);;All files (*)"
PLAYBACK_RATES = [0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 4.0]
SPEED_PRESETS = [0.25, 0.5, 1.0, 2.0, 4.0]


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Simple Montage")
        self.resize(1200, 780)
        self.setAcceptDrops(True)

        self.timeline = Timeline()
        self.info: MediaInfo | None = None
        self.position = 0.0
        self.mark_in: float | None = None
        self.mark_out: float | None = None
        self.selected = -1
        self.playback_rate = 1.0
        self._skip_target: float | None = None
        self._skip_started = 0.0
        self._export: ExportRunner | None = None

        self.player = MpvPlayer()
        self.timeline_view = TimelineWidget(self.timeline)

        self._build_actions()
        self._build_ui()
        self._connect()
        self._refresh()

    # ================================================================ UI setup
    def _action(self, text: str, shortcut, slot, menu) -> QAction:
        action = QAction(text, self)
        if shortcut:
            seqs = shortcut if isinstance(shortcut, list) else [shortcut]
            action.setShortcuts([QKeySequence(s) for s in seqs])
        action.triggered.connect(slot)
        menu.addAction(action)
        self.addAction(action)  # shortcuts work even when the menu bar is hidden
        return action

    def _build_actions(self) -> None:
        m_file = self.menuBar().addMenu("&File")
        self.act_open = self._action("&Open video…", QKeySequence.StandardKey.Open, self.open_dialog, m_file)
        self.act_export = self._action("&Export…", "Ctrl+E", self.export_dialog, m_file)
        m_file.addSeparator()
        self._action("&Quit", QKeySequence.StandardKey.Quit, self.close, m_file)

        m_edit = self.menuBar().addMenu("&Edit")
        self.act_undo = self._action("&Undo", QKeySequence.StandardKey.Undo, self.undo, m_edit)
        self.act_redo = self._action("&Redo", ["Ctrl+Y", "Ctrl+Shift+Z"], self.redo, m_edit)
        m_edit.addSeparator()
        self.act_split = self._action("&Split at playhead", "S", self.split, m_edit)
        self.act_delete = self._action("&Delete selected segment", [QKeySequence.StandardKey.Delete, "Backspace"],
                                       self.delete_selected, m_edit)
        m_edit.addSeparator()
        self._action("Set &In mark", "I", self.set_in, m_edit)
        self._action("Set &Out mark", "O", self.set_out, m_edit)
        self._action("Clear marks", "Escape", self.clear_marks, m_edit)
        self.act_cut_range = self._action("Cut out In → Out", "X", self.cut_range, m_edit)
        self.act_keep_range = self._action("Keep only In → Out", "K", self.keep_range, m_edit)

        m_play = self.menuBar().addMenu("&Playback")
        self._action("Play / Pause", "Space", self.toggle_play, m_play)
        self._action("Next frame", "Right", self.player.frame_step, m_play)
        self._action("Previous frame", "Left", self.player.frame_back_step, m_play)
        self._action("Forward 1 second", "Shift+Right", lambda: self.seek(self.position + 1), m_play)
        self._action("Back 1 second", "Shift+Left", lambda: self.seek(self.position - 1), m_play)
        self._action("Next cut", "Down", self.next_cut, m_play)
        self._action("Previous cut", "Up", self.previous_cut, m_play)
        self._action("Go to start", "Home", lambda: self.seek(0), m_play)
        self._action("Go to end", "End", lambda: self.seek(self.timeline.duration), m_play)

    def _button(self, text: str, tooltip: str, slot) -> QPushButton:
        btn = QPushButton(text)
        btn.setToolTip(tooltip)
        btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # keep Space/arrows for the shortcuts
        btn.clicked.connect(slot)
        return btn

    def _build_ui(self) -> None:
        # transport row
        self.btn_prev = self._button("◀|", "Previous frame (Left)", self.player.frame_back_step)
        self.btn_play = self._button("▶", "Play / Pause (Space)", self.toggle_play)
        self.btn_next = self._button("|▶", "Next frame (Right)", self.player.frame_step)
        self.time_label = QLabel()
        self.time_label.setMinimumWidth(240)

        self.rate_combo = QComboBox()
        self.rate_combo.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.rate_combo.setToolTip("Preview playback speed (does not affect the export)")
        for rate in PLAYBACK_RATES:
            self.rate_combo.addItem(format_speed(rate), rate)
        self.rate_combo.setCurrentIndex(PLAYBACK_RATES.index(1.0))

        self.preview_edits = QCheckBox("Preview edits")
        self.preview_edits.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.preview_edits.setChecked(True)
        self.preview_edits.setToolTip("Skip cut-out parts and apply segment speeds while playing")

        transport = QHBoxLayout()
        for w in (self.btn_prev, self.btn_play, self.btn_next):
            transport.addWidget(w)
        transport.addWidget(self.time_label)
        transport.addStretch()
        transport.addWidget(QLabel("Playback:"))
        transport.addWidget(self.rate_combo)
        transport.addWidget(self.preview_edits)

        # edit row
        self.btn_split = self._button("✂ Split (S)", "Split the segment at the playhead", self.split)
        self.btn_delete = self._button("🗑 Delete (Del)", "Cut out the selected segment", self.delete_selected)
        self.btn_in = self._button("[ In (I)", "Set In mark at the playhead", self.set_in)
        self.btn_out = self._button("Out (O) ]", "Set Out mark at the playhead", self.set_out)
        self.btn_cut_range = self._button("Cut In→Out (X)", "Remove everything between the marks", self.cut_range)
        self.btn_keep_range = self._button("Keep In→Out (K)", "Remove everything outside the marks", self.keep_range)

        self.segment_label = QLabel()
        self.speed_spin = QDoubleSpinBox()
        self.speed_spin.setRange(MIN_SPEED, MAX_SPEED)
        self.speed_spin.setSingleStep(0.05)
        self.speed_spin.setDecimals(2)
        self.speed_spin.setSuffix("×")
        self.speed_spin.setToolTip("Speed of the selected segment in the exported video")
        self.speed_spin.setKeyboardTracking(False)
        self.preset_buttons = [
            self._button(format_speed(s), f"Set selected segment to {format_speed(s)}",
                         lambda _=False, s=s: self.set_segment_speed(s))
            for s in SPEED_PRESETS
        ]

        edit_row = QHBoxLayout()
        for w in (self.btn_split, self.btn_delete, self.btn_in, self.btn_out,
                  self.btn_cut_range, self.btn_keep_range):
            edit_row.addWidget(w)
        edit_row.addStretch()

        segment_row = QHBoxLayout()
        segment_row.addWidget(self.segment_label)
        segment_row.addStretch()
        segment_row.addWidget(QLabel("Segment speed:"))
        segment_row.addWidget(self.speed_spin)
        for b in self.preset_buttons:
            segment_row.addWidget(b)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.addWidget(self.player, stretch=1)
        layout.addLayout(transport)
        layout.addWidget(self.timeline_view)
        layout.addLayout(edit_row)
        layout.addLayout(segment_row)
        self.setCentralWidget(central)

        self.output_label = QLabel()
        self.statusBar().addPermanentWidget(self.output_label)

    def _connect(self) -> None:
        self.player.position_changed.connect(self._on_position)
        self.player.pause_changed.connect(self._on_pause_changed)
        self.player.file_loaded.connect(lambda: self.seek(0))
        self.timeline_view.seek_requested.connect(self.seek)
        self.timeline_view.segment_clicked.connect(self.select)
        self.rate_combo.currentIndexChanged.connect(self._on_rate_changed)
        self.preview_edits.toggled.connect(lambda _: self._update_player_speed())
        self.speed_spin.valueChanged.connect(self.set_segment_speed)

    # ================================================================== files
    def open_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open video", "", VIDEO_FILTER)
        if path:
            self.open_file(path)

    def open_file(self, path: str) -> None:
        ffprobe = find_ffprobe()
        if not ffprobe:
            QMessageBox.critical(self, "ffprobe not found",
                                 "Put ffprobe.exe (and ffmpeg.exe) into the 'bin' folder next to the app.")
            return
        try:
            info = probe(ffprobe, path)
        except (ProbeError, OSError, ValueError) as exc:
            QMessageBox.critical(self, "Cannot open video", str(exc))
            return
        self.info = info
        self.timeline.reset(info.duration, info.fps)
        self.position = 0.0
        self.mark_in = self.mark_out = None
        self.selected = -1
        self.timeline_view.reset_view()
        self.player.load(path)
        self.setWindowTitle(f"{Path(path).name} — Simple Montage")
        fps = f"{info.fps:.3f}".rstrip("0").rstrip(".")
        self.statusBar().showMessage(
            f"{info.width}×{info.height} · {fps} fps · {format_time(info.duration)}"
            + ("" if info.has_audio else " · no audio")
        )
        self._refresh()

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802
        urls = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        if urls:
            self.open_file(urls[0])

    # ================================================================ export
    def export_dialog(self) -> None:
        if not self.info or not self.timeline.segments:
            return
        ffmpeg = find_ffmpeg()
        if not ffmpeg:
            QMessageBox.critical(self, "FFmpeg not found", "Put ffmpeg.exe into the 'bin' folder next to the app.")
            return
        self.player.pause()
        choice = ExportDialog(self.timeline, self.info.has_audio, self.mark_in, self.mark_out, self)
        if choice.exec() != ExportDialog.DialogCode.Accepted:
            return
        opts = choice.options()

        src = Path(self.info.path)
        if opts.mode == ExportMode.AUDIO_ONLY:
            suggested = str(src.with_name(f"{src.stem}_sound.mp3"))
            filters = "MP3 audio (*.mp3);;WAV audio (*.wav);;M4A audio (*.m4a)"
            allowed = tuple(AUDIO_FORMATS)
        else:
            suggested = str(src.with_name(f"{src.stem}_edit.mp4"))
            filters = "MP4 video (*.mp4)"
            allowed = (".mp4",)
        out, chosen_filter = QFileDialog.getSaveFileName(self, "Export", suggested, filters)
        if not out:
            return
        if Path(out).suffix.lower() not in allowed:  # add the extension of the chosen type
            out += "." + chosen_filter.split("*.")[-1].rstrip(")")
        if os.path.abspath(out) == os.path.abspath(self.info.path):
            QMessageBox.warning(self, "Export", "Choose a different file than the source video.")
            return
        args = build_ffmpeg_args(ffmpeg, self.info, opts.segments, out, mode=opts.mode)
        output_duration = sum(seg.output_duration for seg in opts.segments)

        dialog = QProgressDialog("Exporting…", "Cancel", 0, 1000, self)
        dialog.setWindowTitle("Export")
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
        dialog.setMinimumDuration(0)
        dialog.setAutoClose(False)
        dialog.setAutoReset(False)

        runner = ExportRunner(args, output_duration, self)
        runner.progress.connect(lambda f: dialog.setValue(int(f * 1000)))
        dialog.canceled.connect(runner.cancel)

        def done(ok: bool, message: str) -> None:
            dialog.close()
            self._export = None
            if ok:
                QMessageBox.information(self, "Export finished", f"Saved to:\n{out}")
            elif message != "Export cancelled.":
                QMessageBox.critical(self, "Export failed", message)

        runner.finished.connect(done)
        self._export = runner
        runner.start()

    # =============================================================== playback
    def toggle_play(self) -> None:
        if not self.info:
            return
        if not self.player.paused:
            self.player.pause()
            return
        t = self.position
        if t >= self.timeline.duration - self.timeline.frame_duration:
            t = 0.0  # at the end: start over
        if self.preview_edits.isChecked() and self.timeline.segments:
            if self.timeline.index_at(t) is None:
                nxt = self.timeline.next_index_after(t)
                t = self.timeline.segments[nxt if nxt is not None else 0].start
        if abs(t - self.position) > 1e-6:
            self.seek(t)
        self._update_player_speed(t)
        self.player.play()

    def seek(self, t: float) -> None:
        if not self.info:
            return
        t = min(max(t, 0.0), self.timeline.duration)
        self.position = t
        self._skip_target = None
        self.player.seek(t)
        self.timeline_view.set_playhead(t)
        self._update_time_label()

    def next_cut(self) -> None:
        later = [p for p in self.timeline.cut_points() if p > self.position + 1e-6]
        if later:
            self.seek(later[0])

    def previous_cut(self) -> None:
        earlier = [p for p in self.timeline.cut_points() if p < self.position - 1e-6]
        if earlier:
            self.seek(earlier[-1])

    def _on_rate_changed(self) -> None:
        self.playback_rate = float(self.rate_combo.currentData())
        self._update_player_speed()

    def _update_player_speed(self, t: float | None = None) -> None:
        t = self.position if t is None else t
        speed = self.playback_rate
        if self.preview_edits.isChecked():
            i = self.timeline.index_at(t)
            if i is not None:
                speed *= self.timeline.segments[i].speed
        self.player.set_speed(speed)

    def _on_pause_changed(self, paused: bool) -> None:
        self.btn_play.setText("▶" if paused else "⏸")

    def _on_position(self, t: float) -> None:
        self.position = t
        self.timeline_view.set_playhead(t)
        self._update_time_label()
        if not self.player.paused and self.preview_edits.isChecked():
            self._follow_edit(t)

    def _follow_edit(self, t: float) -> None:
        """While previewing edits: jump over cut-out parts, apply segment speeds."""
        if self._skip_target is not None:
            arrived = t >= self._skip_target - self.timeline.frame_duration
            if not arrived and time.monotonic() - self._skip_started < 1.0:
                return  # still waiting for our own seek to land
            self._skip_target = None
        if not self.timeline.segments:
            return
        index = self.timeline.index_at(t)
        if index is None:
            nxt = self.timeline.next_index_after(t)
            if nxt is None:
                self.player.pause()
                return
            index = nxt
            self._skip_target = self.timeline.segments[nxt].start
            self._skip_started = time.monotonic()
            self.player.seek(self._skip_target)
        self.player.set_speed(self.playback_rate * self.timeline.segments[index].speed)

    # ================================================================ editing
    def select(self, index: int) -> None:
        self.selected = index
        self._refresh()

    def split(self) -> None:
        if self.timeline.split(self.position):
            index = self.timeline.index_at(self.timeline.snap(self.position))
            self.selected = -1 if index is None else index
            self._refresh()

    def delete_selected(self) -> None:
        if self.timeline.delete(self.selected):
            self.selected = -1
            self._refresh()

    def set_in(self) -> None:
        if not self.info:
            return
        self.mark_in = self.timeline.snap(self.position)
        if self.mark_out is not None and self.mark_out <= self.mark_in:
            self.mark_out = None
        self._refresh()

    def set_out(self) -> None:
        if not self.info:
            return
        self.mark_out = self.timeline.snap(self.position)
        if self.mark_in is not None and self.mark_in >= self.mark_out:
            self.mark_in = None
        self._refresh()

    def clear_marks(self) -> None:
        self.mark_in = self.mark_out = None
        self._refresh()

    def _marked_range(self) -> tuple[float, float] | None:
        if self.mark_in is None and self.mark_out is None:
            return None
        start = self.mark_in if self.mark_in is not None else 0.0
        end = self.mark_out if self.mark_out is not None else self.timeline.duration
        return start, end

    def cut_range(self) -> None:
        marked = self._marked_range()
        if marked and self.timeline.delete_range(*marked):
            self.selected = -1
            self.clear_marks()

    def keep_range(self) -> None:
        marked = self._marked_range()
        if marked and self.timeline.keep_range(*marked):
            self.selected = -1
            self.clear_marks()

    def set_segment_speed(self, speed: float) -> None:
        if self.timeline.set_speed(self.selected, speed):
            self._update_player_speed()
            self._refresh()

    def undo(self) -> None:
        if self.timeline.undo():
            self.selected = -1
            self._refresh()

    def redo(self) -> None:
        if self.timeline.redo():
            self.selected = -1
            self._refresh()

    # ================================================================ display
    def _update_time_label(self) -> None:
        if not self.info:
            self.time_label.setText("No video loaded")
            return
        out = self.timeline.source_to_output(self.position)
        self.time_label.setText(
            f"{format_time(self.position)} / {format_time(self.timeline.duration)}"
            f"   (output {format_time(out)})"
        )

    def _refresh(self) -> None:
        """Sync every widget with the current model state."""
        tl = self.timeline
        loaded = self.info is not None
        if self.selected >= len(tl.segments):
            self.selected = -1
        has_sel = self.selected >= 0
        has_marks = self.mark_in is not None or self.mark_out is not None

        self.timeline_view.set_selected(self.selected)
        self.timeline_view.set_marks(self.mark_in, self.mark_out)
        self.timeline_view.update()

        for w in (self.btn_prev, self.btn_play, self.btn_next, self.btn_split, self.btn_in, self.btn_out):
            w.setEnabled(loaded)
        self.act_export.setEnabled(loaded and bool(tl.segments))
        self.act_undo.setEnabled(tl.can_undo)
        self.act_redo.setEnabled(tl.can_redo)
        self.btn_delete.setEnabled(has_sel)
        self.btn_cut_range.setEnabled(has_marks)
        self.btn_keep_range.setEnabled(has_marks)
        self.speed_spin.setEnabled(has_sel)
        for b in self.preset_buttons:
            b.setEnabled(has_sel)

        if has_sel:
            seg = tl.segments[self.selected]
            self.segment_label.setText(
                f"Segment {self.selected + 1}/{len(tl.segments)}: "
                f"{format_time(seg.start)} – {format_time(seg.end)}  →  {format_time(seg.output_duration)} long"
            )
            self.speed_spin.blockSignals(True)
            self.speed_spin.setValue(seg.speed)
            self.speed_spin.blockSignals(False)
        else:
            self.segment_label.setText("Click a segment on the timeline to select it" if loaded else "")

        if loaded:
            self.output_label.setText(
                f"Output: {format_time(tl.output_duration)} · {len(tl.segments)} segment(s)"
            )
        else:
            self.output_label.setText("")
        self._update_time_label()

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._export is not None:
            self._export.cancel()
        self.player.shutdown()
        super().closeEvent(event)
