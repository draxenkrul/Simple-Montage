"""Main application window: preview, transport controls, timeline, segment panel."""

from __future__ import annotations

import os
import time
from fractions import Fraction
from pathlib import Path

from PySide6.QtCore import QSettings, Qt
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
    QSlider,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from simple_montage.export import AUDIO_FORMATS, ExportMode, build_ffmpeg_args
from simple_montage.export_dialog import ExportDialog
from simple_montage.export_runner import ExportRunner
from simple_montage.model import MAX_SPEED, MAX_ZOOM, MIN_SPEED, MIN_ZOOM, Overlay, Timeline
from simple_montage.panels import LightPanel, OverlayPanel
from simple_montage.player import MpvPlayer, OverlayPreview, VideoFrame
from simple_montage.probe import MediaInfo, ProbeError, probe
from simple_montage.timecode import format_speed, format_time
from simple_montage.timeline_widget import TimelineWidget
from simple_montage.tools import find_ffmpeg, find_ffprobe
from simple_montage.zoom_navigator import ZoomNavigator

VIDEO_FILTER = "Videos (*.mp4 *.mov *.mkv *.avi *.webm *.m4v *.mts *.m2ts *.wmv *.flv);;All files (*)"
PLAYBACK_RATES = [0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 4.0]
SPEED_PRESETS = [0.25, 0.5, 1.0, 2.0, 4.0]
VOLUME_STEP = 5
ZOOM_PRESETS = [1.0, 1.5, 2.0, 3.0]


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
        self.selected_overlay = -1
        self.muted = False
        self.playback_rate = 1.0
        self._skip_target: float | None = None
        self._skip_started = 0.0
        self._export: ExportRunner | None = None

        self.player = MpvPlayer()
        self.video_frame = VideoFrame(self.player)
        self.overlay_preview = OverlayPreview(self.video_frame, lambda: self._update_player_view())
        self.timeline_view = TimelineWidget(self.timeline)

        self._build_actions()
        self._build_ui()
        self._connect()
        self._restore_volume()
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
        self.act_add_overlay = self._action("Add &overlay video…", "Ctrl+Shift+O", self.add_overlay_dialog, m_file)
        self.act_export = self._action("&Export…", "Ctrl+E", self.export_dialog, m_file)
        m_file.addSeparator()
        self._action("&Quit", QKeySequence.StandardKey.Quit, self.close, m_file)

        m_edit = self.menuBar().addMenu("&Edit")
        self.act_undo = self._action("&Undo", QKeySequence.StandardKey.Undo, self.undo, m_edit)
        self.act_redo = self._action("&Redo", ["Ctrl+Y", "Ctrl+Shift+Z"], self.redo, m_edit)
        m_edit.addSeparator()
        self.act_split = self._action("&Split at playhead", "S", self.split, m_edit)
        self.act_delete = self._action("&Delete selected segment / overlay", [QKeySequence.StandardKey.Delete, "Backspace"],
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
        m_play.addSeparator()
        self._action("Volume up", ["+", "="], lambda: self.change_volume(VOLUME_STEP), m_play)
        self._action("Volume down", "-", lambda: self.change_volume(-VOLUME_STEP), m_play)
        self._action("Mute / unmute", "M", self.toggle_mute, m_play)

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

        self.btn_mute = self._button("🔊", "Mute / unmute (M)", self.toggle_mute)
        self.btn_mute.setFixedWidth(36)
        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setFixedWidth(120)
        self.volume_slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # arrows stay frame-stepping
        self.volume_slider.setToolTip("Preview volume (+ / −). Does not change the exported sound.")
        self.volume_label = QLabel()
        self.volume_label.setMinimumWidth(36)

        transport = QHBoxLayout()
        for w in (self.btn_prev, self.btn_play, self.btn_next):
            transport.addWidget(w)
        transport.addWidget(self.time_label)
        transport.addStretch()
        transport.addWidget(self.btn_mute)
        transport.addWidget(self.volume_slider)
        transport.addWidget(self.volume_label)
        transport.addSpacing(12)
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

        self.zoom_spin = QDoubleSpinBox()
        self.zoom_spin.setRange(MIN_ZOOM, MAX_ZOOM)
        self.zoom_spin.setSingleStep(0.1)
        self.zoom_spin.setDecimals(2)
        self.zoom_spin.setSuffix("×")
        self.zoom_spin.setToolTip("Zoom of the selected segment (1× = whole picture)")
        self.zoom_spin.setKeyboardTracking(False)
        self.zoom_buttons = [
            self._button(format_speed(z), f"Zoom the selected segment to {format_speed(z)}",
                         lambda _=False, z=z: self.set_segment_zoom(z))
            for z in ZOOM_PRESETS
        ]
        self.btn_center = self._button("Center", "Show the middle of the picture", lambda: self.set_segment_pan(0.5, 0.5))
        self.navigator = ZoomNavigator()

        edit_row = QHBoxLayout()
        for w in (self.btn_split, self.btn_delete, self.btn_in, self.btn_out,
                  self.btn_cut_range, self.btn_keep_range):
            edit_row.addWidget(w)
        edit_row.addStretch()
        edit_row.addWidget(self.segment_label)

        segment_row = QHBoxLayout()
        segment_row.addStretch()
        segment_row.addWidget(QLabel("Segment speed:"))
        segment_row.addWidget(self.speed_spin)
        for b in self.preset_buttons:
            segment_row.addWidget(b)

        zoom_row = QHBoxLayout()
        zoom_hint = QLabel("Zoom: pick a level, then drag the box on the right to choose the visible area →")
        zoom_hint.setStyleSheet("color: gray;")
        zoom_row.addWidget(zoom_hint)
        zoom_row.addStretch()
        zoom_row.addWidget(QLabel("Segment zoom:"))
        zoom_row.addWidget(self.zoom_spin)
        for b in self.zoom_buttons:
            zoom_row.addWidget(b)
        zoom_row.addWidget(self.btn_center)
        zoom_row.addWidget(self.navigator)

        speed_zoom_page = QWidget()
        sz = QVBoxLayout(speed_zoom_page)
        sz.addLayout(segment_row)
        sz.addLayout(zoom_row)
        self.light_panel = LightPanel()
        self.overlay_panel = OverlayPanel()
        self.tabs = QTabWidget()
        self.tabs.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.tabs.addTab(speed_zoom_page, "⏩ Speed && Zoom")
        self.tabs.addTab(self.light_panel, "☀ Light")
        self.tabs.addTab(self.overlay_panel, "🖼 Overlay video")

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.addWidget(self.video_frame, stretch=1)
        layout.addLayout(transport)
        layout.addWidget(self.timeline_view)
        layout.addLayout(edit_row)
        layout.addWidget(self.tabs)
        self.setCentralWidget(central)

        self.output_label = QLabel()
        self.statusBar().addPermanentWidget(self.output_label)

    def _connect(self) -> None:
        self.player.position_changed.connect(self._on_position)
        self.player.pause_changed.connect(self._on_pause_changed)
        self.player.file_loaded.connect(lambda: self.seek(0))
        self.timeline_view.seek_requested.connect(self.seek)
        self.timeline_view.segment_clicked.connect(self.select)
        self.timeline_view.overlay_clicked.connect(self.select_overlay)
        self.timeline_view.overlay_moved.connect(lambda i, t: self.change_overlay({"start": t}))
        self.light_panel.light_changed.connect(self.set_segment_light)
        self.light_panel.reset_requested.connect(self.reset_segment_light)
        self.overlay_panel.add_requested.connect(self.add_overlay_dialog)
        self.overlay_panel.delete_requested.connect(self.delete_overlay)
        self.overlay_panel.overlay_changed.connect(self.change_overlay)
        self.rate_combo.currentIndexChanged.connect(self._on_rate_changed)
        self.preview_edits.toggled.connect(lambda _: (self._update_player_speed(), self._update_player_view()))
        self.volume_slider.valueChanged.connect(self._on_volume_changed)
        self.speed_spin.valueChanged.connect(self.set_segment_speed)
        self.zoom_spin.valueChanged.connect(self.set_segment_zoom)
        self.navigator.view_changed.connect(self.set_segment_pan)

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
        self.selected_overlay = -1
        self.overlay_preview.hide()
        self.timeline_view.reset_view()
        aspect = info.width * float(Fraction(info.sar)) / info.height if info.height else 16 / 9
        self.aspect = aspect
        self.video_frame.set_aspect(aspect)
        self.navigator.set_aspect(aspect)
        self.player.set_view(1.0, 0.5, 0.5)
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
        args = build_ffmpeg_args(ffmpeg, self.info, opts.segments, out, mode=opts.mode,
                                 overlays=self.timeline.overlays)
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

    def _update_player_view(self, t: float | None = None) -> None:
        """Preview the zoom of the segment under the playhead."""
        t = self.position if t is None else t
        preview = self.preview_edits.isChecked()
        i = self.timeline.index_at(t) if preview else None
        if i is None:
            self.player.set_view(1.0, 0.5, 0.5)
            self.player.set_light(None)
        else:
            seg = self.timeline.segments[i]
            self.player.set_view(seg.zoom, seg.pan_x, seg.pan_y)
            self.player.set_light(seg.light if seg.has_light else None)
        k = self.timeline.overlay_at(t) if preview else None
        self.overlay_preview.update(
            None if k is None else self.timeline.overlays[k], t,
            playing=not self.player.paused, speed=self.player.speed,
            volume=self.volume_slider.value(), muted=self.muted,
        )

    def _on_pause_changed(self, paused: bool) -> None:
        self.btn_play.setText("▶" if paused else "⏸")
        self._update_player_view()

    def _on_position(self, t: float) -> None:
        self.position = t
        self.timeline_view.set_playhead(t)
        self._update_time_label()
        self._update_player_view(t)
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

    # ================================================================= volume
    def _restore_volume(self) -> None:
        settings = QSettings("SimpleMontage", "SimpleMontage")
        volume = settings.value("volume", 80, type=int)
        self.muted = False  # set the level first so restoring it does not unmute
        self.volume_slider.setValue(volume)
        self._on_volume_changed(self.volume_slider.value())  # apply even if unchanged
        self.muted = settings.value("muted", False, type=bool)
        self.player.set_muted(self.muted)
        self._update_mute_button()

    def _on_volume_changed(self, value: int) -> None:
        self.player.set_volume(value)
        if self.info:
            self._update_player_view()
        self.volume_label.setText(f"{value}%")
        if self.muted and value > 0:  # moving the slider unmutes, like most players
            self.toggle_mute()
        self._update_mute_button()

    def change_volume(self, delta: int) -> None:
        self.volume_slider.setValue(self.volume_slider.value() + delta)

    def toggle_mute(self) -> None:
        self.muted = not self.muted
        self.player.set_muted(self.muted)
        self._update_mute_button()
        if self.info:
            self._update_player_view()

    def _update_mute_button(self) -> None:
        silent = self.muted or self.volume_slider.value() == 0
        self.btn_mute.setText("🔇" if silent else "🔊")

    # ================================================================ editing
    def select(self, index: int) -> None:
        self.selected = index
        self.selected_overlay = -1
        if index >= 0 and self.tabs.currentWidget() is self.overlay_panel:
            self.tabs.setCurrentIndex(0)
        self._refresh()

    def select_overlay(self, index: int) -> None:
        self.selected_overlay = index
        self.selected = -1
        self.tabs.setCurrentWidget(self.overlay_panel)
        self._refresh()

    def split(self) -> None:
        if self.timeline.split(self.position):
            index = self.timeline.index_at(self.timeline.snap(self.position))
            self.selected = -1 if index is None else index
            self._refresh()

    def delete_selected(self) -> None:
        if self.selected_overlay >= 0:
            self.delete_overlay()
            return
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

    def _show_selected(self) -> None:
        """Move the playhead into the selected segment so its zoom is visible."""
        seg = self.timeline.segments[self.selected]
        if not seg.contains(self.position):
            self.seek(seg.start)

    def set_segment_zoom(self, zoom: float) -> None:
        if self.timeline.set_zoom(self.selected, zoom):
            self._show_selected()
            self._update_player_view()
            self._refresh()

    def set_segment_pan(self, pan_x: float, pan_y: float) -> None:
        if 0 <= self.selected < len(self.timeline.segments):
            seg = self.timeline.segments[self.selected]
            if self.timeline.set_zoom(self.selected, seg.zoom, pan_x, pan_y):
                self._show_selected()
                self._update_player_view()
                self._refresh()

    # ------------------------------------------------------------- lighting
    def set_segment_light(self, values: dict) -> None:
        if self.timeline.set_light(self.selected, **values):
            self._show_selected()
            self._update_player_view()
            self._refresh()

    def reset_segment_light(self) -> None:
        if self.timeline.reset_light(self.selected):
            self._update_player_view()
            self._refresh()

    # ------------------------------------------------------------- overlays
    def add_overlay_dialog(self) -> None:
        if not self.info:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Add overlay video", "", VIDEO_FILTER)
        if path:
            self.add_overlay(path)

    def add_overlay(self, path: str) -> None:
        """Put ``path`` on top of the main video, starting at the playhead."""
        try:
            info = probe(find_ffprobe() or "ffprobe", path)
        except (ProbeError, OSError, ValueError) as exc:
            QMessageBox.critical(self, "Cannot use this video", str(exc))
            return
        index = self.timeline.add_overlay(Overlay(
            path, info.duration, info.width, info.height, info.has_audio,
            start=self.position, length=info.duration,
        ))
        self.select_overlay(index)
        self._update_player_view()

    def change_overlay(self, changes: dict) -> None:
        if self.timeline.update_overlay(self.selected_overlay, **changes):
            self._update_player_view()
            self._refresh()

    def delete_overlay(self) -> None:
        if self.timeline.remove_overlay(self.selected_overlay):
            self.selected_overlay = -1
            self._update_player_view()
            self._refresh()

    def undo(self) -> None:
        if self.timeline.undo():
            self.selected = self.selected_overlay = -1
            self._update_player_view()
            self._refresh()

    def redo(self) -> None:
        if self.timeline.redo():
            self.selected = self.selected_overlay = -1
            self._update_player_view()
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
        if self.selected_overlay >= len(tl.overlays):
            self.selected_overlay = -1
        has_sel = self.selected >= 0
        has_marks = self.mark_in is not None or self.mark_out is not None

        self.timeline_view.set_selected(self.selected)
        self.timeline_view.set_selected_overlay(self.selected_overlay)
        self.light_panel.set_segment(tl.segments[self.selected] if self.selected >= 0 else None)
        self.overlay_panel.set_overlay(
            tl.overlays[self.selected_overlay] if self.selected_overlay >= 0 else None,
            getattr(self, "aspect", 16 / 9), tl.duration)
        self.overlay_panel.add_button.setEnabled(loaded)
        self.act_add_overlay.setEnabled(loaded)
        self.timeline_view.set_marks(self.mark_in, self.mark_out)
        self.timeline_view.update()

        for w in (self.btn_prev, self.btn_play, self.btn_next, self.btn_split, self.btn_in, self.btn_out):
            w.setEnabled(loaded)
        self.act_export.setEnabled(loaded and bool(tl.segments))
        self.act_undo.setEnabled(tl.can_undo)
        self.act_redo.setEnabled(tl.can_redo)
        self.btn_delete.setEnabled(has_sel or self.selected_overlay >= 0)
        self.btn_cut_range.setEnabled(has_marks)
        self.btn_keep_range.setEnabled(has_marks)
        for w in (self.speed_spin, self.zoom_spin, self.navigator, *self.preset_buttons, *self.zoom_buttons):
            w.setEnabled(has_sel)
        self.btn_center.setEnabled(has_sel and tl.segments[self.selected].zoom > 1)

        if has_sel:
            seg = tl.segments[self.selected]
            self.segment_label.setText(
                f"Segment {self.selected + 1}/{len(tl.segments)}: "
                f"{format_time(seg.start)} – {format_time(seg.end)}  →  {format_time(seg.output_duration)} long"
            )
            for spin, value in ((self.speed_spin, seg.speed), (self.zoom_spin, seg.zoom)):
                spin.blockSignals(True)
                spin.setValue(value)
                spin.blockSignals(False)
            self.navigator.set_view(seg.zoom, seg.pan_x, seg.pan_y)
        else:
            self.navigator.set_view(1.0, 0.5, 0.5)
            if self.selected_overlay >= 0:
                self.segment_label.setText(f"Overlay {self.selected_overlay + 1}/{len(tl.overlays)} selected")
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
        settings = QSettings("SimpleMontage", "SimpleMontage")
        settings.setValue("volume", self.volume_slider.value())
        settings.setValue("muted", self.muted)
        self.overlay_preview.shutdown()
        self.player.shutdown()
        super().closeEvent(event)
