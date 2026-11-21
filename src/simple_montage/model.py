"""Edit model: which parts of the source video are kept, and how they look.

The timeline is expressed in *source* time. It holds an ordered list of
non-overlapping :class:`Segment` objects. Anything not covered by a segment is
cut out of the final video. Every segment has its own speed, zoom and
lighting. :class:`Overlay` clips are other videos shown on top of the main
one, placed on the same source-time axis.

Several videos can be combined: their files (``sources``) are laid end to
end, so the "source time" axis runs through clip 1, then clip 2, and so on.
A segment never crosses from one clip into the next.

This module has no Qt or FFmpeg dependency so it can be unit-tested directly.
"""

from __future__ import annotations

import copy
from bisect import bisect_right
from dataclasses import dataclass, replace

from simple_montage.probe import MediaInfo

MIN_SPEED = 0.1
MAX_SPEED = 10.0
MIN_ZOOM = 1.0
MAX_ZOOM = 5.0

# Lighting, in FFmpeg "eq" filter units: name -> (minimum, default, maximum)
LIGHT_RANGES = {
    "brightness": (-0.5, 0.0, 0.5),
    "contrast": (0.5, 1.0, 2.0),
    "saturation": (0.0, 1.0, 2.0),
    "gamma": (0.5, 1.0, 2.0),
}

MIN_OVERLAY_SCALE = 0.05
MAX_OVERLAY_SCALE = 1.0


@dataclass
class Segment:
    """A kept piece of the source video, ``[start, end)`` in source seconds.

    ``zoom`` magnifies the picture (1 = whole frame, 2 = half the width and
    height, ...). ``pan_x``/``pan_y`` choose which part is shown when zoomed:
    0 = left/top edge, 0.5 = centre, 1 = right/bottom edge.

    ``brightness``/``contrast``/``saturation``/``gamma`` adjust the lighting
    (see :data:`LIGHT_RANGES`).
    """

    start: float
    end: float
    speed: float = 1.0
    zoom: float = 1.0
    pan_x: float = 0.5
    pan_y: float = 0.5
    brightness: float = 0.0
    contrast: float = 1.0
    saturation: float = 1.0
    gamma: float = 1.0

    @property
    def light(self) -> dict[str, float]:
        return {name: getattr(self, name) for name in LIGHT_RANGES}

    @property
    def has_light(self) -> bool:
        return any(abs(getattr(self, n) - d) > 1e-9 for n, (_lo, d, _hi) in LIGHT_RANGES.items())

    @property
    def source_duration(self) -> float:
        return self.end - self.start

    @property
    def output_duration(self) -> float:
        return self.source_duration / self.speed

    def contains(self, t: float) -> bool:
        return self.start <= t < self.end


@dataclass
class Overlay:
    """Another video shown on top of the main one (picture-in-picture).

    It is shown while the main video is between ``start`` and
    ``start + length`` (main source seconds) and plays its own file from
    ``in_point``. Because it is tied to the main source time, cuts and speed
    changes of the main video apply to it too.

    ``scale`` is its width as a fraction of the main picture width.
    ``pos_x``/``pos_y`` place it: 0 = left/top edge, 1 = right/bottom edge.
    """

    path: str
    file_duration: float
    width: int
    height: int
    has_audio: bool
    start: float = 0.0
    in_point: float = 0.0
    length: float = 0.0
    scale: float = 0.35
    pos_x: float = 0.95
    pos_y: float = 0.95
    sound: bool = False

    @property
    def end(self) -> float:
        return self.start + self.length

    @property
    def aspect(self) -> float:
        return self.width / self.height if self.width and self.height else 16 / 9

    def active_at(self, t: float) -> bool:
        return self.start <= t < self.end

    def file_time(self, t: float) -> float:
        """Time in the overlay's own file shown at main source time ``t``."""
        return self.in_point + (t - self.start)


class Timeline:
    """Kept segments of one source video, with undo/redo."""

    def __init__(self, duration: float = 0.0, fps: float = 30.0) -> None:
        self.reset(duration, fps)

    # ------------------------------------------------------------------ setup
    def reset(self, duration: float, fps: float, sources: list[MediaInfo] | None = None) -> None:
        """Start over with the whole source kept as a single segment."""
        self.sources: list[MediaInfo] = list(sources or [])
        self.duration = max(0.0, float(duration))
        self.fps = float(fps) if fps and fps > 0 else 30.0
        self.segments: list[Segment] = (
            [Segment(0.0, self.duration)] if self.duration > 0 else []
        )
        self.overlays: list[Overlay] = []
        self._undo: list[tuple] = []
        self._redo: list[tuple] = []
        self._last_op: object = None

    @classmethod
    def from_media(cls, info: MediaInfo) -> Timeline:
        tl = cls()
        tl.reset(info.duration, info.fps, [info])
        return tl

    # ------------------------------------------------------- combined clips
    @property
    def clip_offsets(self) -> list[float]:
        """Where each source clip starts on the timeline."""
        offsets, t = [], 0.0
        for src in self.sources:
            offsets.append(t)
            t += src.duration
        return offsets

    def clip_at(self, t: float) -> int | None:
        """Index of the source clip at timeline time ``t``."""
        for k, (offset, src) in enumerate(zip(self.clip_offsets, self.sources)):
            if offset <= t < offset + src.duration:
                return k
        return len(self.sources) - 1 if self.sources and t >= self.duration else None

    def append_clip(self, info: MediaInfo) -> int:
        """Add a video to the end of the timeline. Returns its clip index."""
        self._checkpoint()
        start = self.duration
        self.sources.append(info)
        self.duration += info.duration
        self.segments.append(Segment(start, start + info.duration))
        return len(self.sources) - 1

    def remove_clip(self, k: int) -> bool:
        """Take a video off the timeline; later clips move up to close the gap."""
        if not 0 <= k < len(self.sources) or len(self.sources) < 2:
            return False
        self._checkpoint()
        a = self.clip_offsets[k]
        b = a + self.sources[k].duration
        d = b - a
        self.segments = [self._shifted(seg, -d if seg.start >= b else 0.0)
                         for seg in self.segments if not (seg.start >= a and seg.end <= b + 1e-9)]
        self.overlays = [replace(ov, start=ov.start - d if ov.start >= b else ov.start)
                         for ov in self.overlays if not a <= ov.start < b]
        del self.sources[k]
        self.duration -= d
        for ov in self.overlays:
            self._clamp_overlay(ov)
        return True

    def move_clip(self, k: int, step: int) -> bool:
        """Move clip ``k`` earlier (step -1) or later (+1), with its edits and overlays."""
        j = k + step
        if not (0 <= k < len(self.sources) and 0 <= j < len(self.sources)):
            return False
        self._checkpoint()
        old_offsets = self.clip_offsets
        order = list(range(len(self.sources)))
        order[k], order[j] = order[j], order[k]
        new_sources = [self.sources[i] for i in order]
        new_offset_of, t = {}, 0.0
        for i in order:
            new_offset_of[i] = t
            t += self.sources[i].duration
        shift = {i: new_offset_of[i] - old_offsets[i] for i in range(len(self.sources))}

        def clip_of(t: float) -> int:
            for i in range(len(self.sources) - 1, -1, -1):
                if t >= old_offsets[i] - 1e-9:
                    return i
            return 0

        self.segments = sorted((self._shifted(seg, shift[clip_of(seg.start)]) for seg in self.segments),
                               key=lambda seg: seg.start)
        self.overlays = [replace(ov, start=ov.start + shift[clip_of(ov.start)]) for ov in self.overlays]
        self.sources = new_sources
        for ov in self.overlays:
            self._clamp_overlay(ov)
        return True

    @staticmethod
    def _shifted(seg: Segment, d: float) -> Segment:
        return replace(seg, start=seg.start + d, end=seg.end + d)

    # -------------------------------------------------------------- utilities
    @property
    def frame_duration(self) -> float:
        return 1.0 / self.fps

    def snap(self, t: float) -> float:
        """Clamp ``t`` to the source and round it to the nearest frame start."""
        t = min(max(t, 0.0), self.duration)
        snapped = round(t * self.fps) / self.fps
        return min(snapped, self.duration)

    def index_at(self, t: float) -> int | None:
        """Index of the segment containing source time ``t``, or None if cut out."""
        starts = [s.start for s in self.segments]
        i = bisect_right(starts, t) - 1
        if i >= 0 and self.segments[i].contains(t):
            return i
        return None

    def next_index_after(self, t: float) -> int | None:
        """Index of the first segment that starts after ``t``."""
        for i, seg in enumerate(self.segments):
            if seg.start > t:
                return i
        return None

    def gap_at(self, t: float) -> tuple[float, float] | None:
        """The cut-out part ``(start, end)`` containing ``t``, or None if ``t`` is kept."""
        if not 0 <= t < self.duration or self.index_at(t) is not None:
            return None
        start, end = 0.0, self.duration
        for seg in self.segments:
            if seg.end <= t:
                start = max(start, seg.end)
            elif seg.start > t:
                end = min(end, seg.start)
        return start, end

    @property
    def has_cuts(self) -> bool:
        """True once the video is split or something is cut out."""
        return len(self.segments) > 1 or (
            len(self.segments) == 1
            and (self.segments[0].start > 0 or self.segments[0].end < self.duration)
        )

    def cut_points(self) -> list[float]:
        """Sorted unique segment boundaries, including source start and end."""
        points = {0.0, self.duration}
        for seg in self.segments:
            points.add(seg.start)
            points.add(seg.end)
        return sorted(points)

    @property
    def output_duration(self) -> float:
        return sum(seg.output_duration for seg in self.segments)

    def source_to_output(self, t: float) -> float:
        """Map a source time to the time it will appear at in the exported video."""
        out = 0.0
        for seg in self.segments:
            if t >= seg.end:
                out += seg.output_duration
            elif t >= seg.start:
                out += (t - seg.start) / seg.speed
                break
            else:
                break
        return out

    def segments_between(self, a: float, b: float) -> list[Segment]:
        """Copies of the kept segments clipped to timeline range ``[a, b)``.

        Used to export only part of the timeline; the edit itself is unchanged.
        Cut-out parts inside the range stay cut out, speeds are kept.
        """
        a, b = sorted((self.snap(a), self.snap(b)))
        min_len = self.frame_duration * 0.5
        result: list[Segment] = []
        for seg in self.segments:
            start, end = max(seg.start, a), min(seg.end, b)
            if end - start >= min_len:
                result.append(replace(seg, start=start, end=end))
        return result

    # ---------------------------------------------------------------- editing
    def split(self, t: float) -> bool:
        """Split the segment under ``t`` into two. Returns True if it split."""
        t = self.snap(t)
        i = self.index_at(t)
        if i is None:
            return False
        seg = self.segments[i]
        min_len = self.frame_duration * 0.5
        if t - seg.start < min_len or seg.end - t < min_len:
            return False
        self._checkpoint()
        self.segments[i : i + 1] = [
            replace(seg, end=t),
            replace(seg, start=t),
        ]
        return True

    def delete(self, index: int) -> bool:
        """Remove a segment (that part of the source is cut out)."""
        if not 0 <= index < len(self.segments):
            return False
        self._checkpoint()
        del self.segments[index]
        return True

    def delete_range(self, a: float, b: float) -> bool:
        """Cut out source time ``[a, b)`` wherever it is still kept."""
        a, b = sorted((self.snap(a), self.snap(b)))
        if b - a < self.frame_duration * 0.5:
            return False
        new = self._without_range(self.segments, a, b)
        if new == self.segments:
            return False
        self._checkpoint()
        self.segments = new
        return True

    def keep_range(self, a: float, b: float) -> bool:
        """Cut out everything outside source time ``[a, b)``."""
        a, b = sorted((self.snap(a), self.snap(b)))
        if b - a < self.frame_duration * 0.5:
            return False
        new = self._without_range(self.segments, 0.0, a)
        new = self._without_range(new, b, self.duration)
        if new == self.segments:
            return False
        self._checkpoint()
        self.segments = new
        return True

    def set_speed(self, index: int, speed: float) -> bool:
        """Change one segment's speed. Repeated changes merge into one undo step."""
        if not 0 <= index < len(self.segments):
            return False
        speed = min(max(float(speed), MIN_SPEED), MAX_SPEED)
        if abs(self.segments[index].speed - speed) < 1e-9:
            return False
        self._checkpoint(op=("speed", index))
        self.segments[index].speed = speed
        return True

    def set_zoom(self, index: int, zoom: float, pan_x: float | None = None,
                 pan_y: float | None = None) -> bool:
        """Change one segment's zoom and/or visible area.

        Repeated changes (dragging, spinning) merge into one undo step.
        """
        if not 0 <= index < len(self.segments):
            return False
        seg = self.segments[index]
        zoom = min(max(float(zoom), MIN_ZOOM), MAX_ZOOM)
        pan_x = seg.pan_x if pan_x is None else min(max(float(pan_x), 0.0), 1.0)
        pan_y = seg.pan_y if pan_y is None else min(max(float(pan_y), 0.0), 1.0)
        if (abs(seg.zoom - zoom) < 1e-9 and abs(seg.pan_x - pan_x) < 1e-9
                and abs(seg.pan_y - pan_y) < 1e-9):
            return False
        self._checkpoint(op=("zoom", index))
        seg.zoom, seg.pan_x, seg.pan_y = zoom, pan_x, pan_y
        return True

    def set_light(self, index: int, **values: float) -> bool:
        """Change lighting of one segment, e.g. ``set_light(0, brightness=0.1)``.

        Repeated changes (dragging a slider) merge into one undo step.
        """
        if not 0 <= index < len(self.segments):
            return False
        seg = self.segments[index]
        changes = {}
        for name, value in values.items():
            lo, _default, hi = LIGHT_RANGES[name]
            value = min(max(float(value), lo), hi)
            if abs(getattr(seg, name) - value) > 1e-9:
                changes[name] = value
        if not changes:
            return False
        self._checkpoint(op=("light", index))
        for name, value in changes.items():
            setattr(seg, name, value)
        return True

    def reset_light(self, index: int) -> bool:
        return self.set_light(index, **{n: d for n, (_lo, d, _hi) in LIGHT_RANGES.items()})

    # --------------------------------------------------------------- overlays
    def add_overlay(self, overlay: Overlay) -> int:
        """Add an overlay (clamped to fit). Returns its index."""
        self._checkpoint()
        self.overlays.append(overlay)
        self._clamp_overlay(overlay)
        return len(self.overlays) - 1

    def remove_overlay(self, index: int) -> bool:
        if not 0 <= index < len(self.overlays):
            return False
        self._checkpoint()
        del self.overlays[index]
        return True

    def update_overlay(self, index: int, **changes) -> bool:
        """Change overlay fields (start, in_point, length, scale, pos_x, pos_y, sound).

        Repeated changes to the same overlay (dragging) merge into one undo step.
        """
        if not 0 <= index < len(self.overlays):
            return False
        ov = self.overlays[index]
        trial = replace(ov, **changes)
        self._clamp_overlay(trial)
        if trial == ov:
            return False
        self._checkpoint(op=("overlay", index))
        self.overlays[index] = trial
        return True

    def _clamp_overlay(self, ov: Overlay) -> None:
        frame = self.frame_duration
        ov.start = self.snap(min(max(ov.start, 0.0), max(0.0, self.duration - frame)))
        ov.in_point = min(max(ov.in_point, 0.0), max(0.0, ov.file_duration - frame))
        longest = min(ov.file_duration - ov.in_point, self.duration - ov.start)
        ov.length = min(max(ov.length, frame), max(frame, longest))
        ov.scale = min(max(ov.scale, MIN_OVERLAY_SCALE), MAX_OVERLAY_SCALE)
        ov.pos_x = min(max(ov.pos_x, 0.0), 1.0)
        ov.pos_y = min(max(ov.pos_y, 0.0), 1.0)
        ov.sound = bool(ov.sound) and ov.has_audio

    def overlay_at(self, t: float) -> int | None:
        """Index of the topmost overlay shown at source time ``t``."""
        for i in range(len(self.overlays) - 1, -1, -1):
            if self.overlays[i].active_at(t):
                return i
        return None

    def _without_range(self, segments: list[Segment], a: float, b: float) -> list[Segment]:
        min_len = self.frame_duration * 0.5
        result: list[Segment] = []
        for seg in segments:
            if seg.end <= a or seg.start >= b:
                result.append(copy.copy(seg))
                continue
            if a - seg.start >= min_len:
                result.append(replace(seg, end=a))
            if seg.end - b >= min_len:
                result.append(replace(seg, start=b))
        return result

    # ------------------------------------------------------------- undo/redo
    def _checkpoint(self, op: object = None) -> None:
        """Save the current state before a change.

        Consecutive changes with the same non-None ``op`` key (e.g. dragging a
        speed spin box) are merged into a single undo step.
        """
        if op is not None and op == self._last_op:
            return
        self._undo.append(self._snapshot())
        self._redo.clear()
        self._last_op = op

    def _snapshot(self) -> tuple:
        return copy.deepcopy((self.segments, self.overlays, self.sources, self.duration))

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo(self) -> bool:
        if not self._undo:
            return False
        self._redo.append(self._snapshot())
        self.segments, self.overlays, self.sources, self.duration = self._undo.pop()
        self._last_op = None
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        self._undo.append(self._snapshot())
        self.segments, self.overlays, self.sources, self.duration = self._redo.pop()
        self._last_op = None
        return True
